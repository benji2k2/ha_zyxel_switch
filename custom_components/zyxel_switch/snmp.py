"""A small, read-only SNMPv2c client.

Home Assistant's own ``snmp`` integration goes through pysnmp, which builds an
engine and loads MIB modules before the first packet leaves. For a switch that
only needs a handful of table walks this is the expensive part, so this module
speaks the wire format directly: BER-encoded GetRequest/GetBulkRequest over one
UDP socket, matched to responses by request id.

Only what reading a switch needs is implemented. There is no SET, no SNMPv3 and
no MIB parsing; OIDs are plain dotted strings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import itertools
import random

# BER universal and SNMP application tags.
_INTEGER = 0x02
_OCTET_STRING = 0x04
_NULL = 0x05
_OID = 0x06
_SEQUENCE = 0x30
_IP_ADDRESS = 0x40
_COUNTER32 = 0x41
_GAUGE32 = 0x42
_TIMETICKS = 0x43
_OPAQUE = 0x44
_COUNTER64 = 0x46
_NO_SUCH_OBJECT = 0x80
_NO_SUCH_INSTANCE = 0x81
_END_OF_MIB_VIEW = 0x82

_GET_REQUEST = 0xA0
_GET_RESPONSE = 0xA2
_GET_BULK_REQUEST = 0xA5

_VERSION_2C = 1
_ERROR_TOO_BIG = 1

_UNSIGNED_TAGS = {_COUNTER32, _GAUGE32, _TIMETICKS, _COUNTER64}


class SnmpError(Exception):
    """Base class for everything this client raises."""


class SnmpTimeout(SnmpError):
    """The agent did not answer, even after retrying."""


class SnmpProtocolError(SnmpError):
    """The agent answered with something that could not be decoded."""


class SnmpErrorStatus(SnmpError):
    """The agent answered, but flagged the request with an error status."""

    def __init__(self, status: int, index: int) -> None:
        super().__init__(f"agent returned error-status {status} at varbind {index}")
        self.status = status
        self.index = index


class _EndOfView:
    """Marker for noSuchObject, noSuchInstance and endOfMibView values."""

    def __init__(self, tag: int) -> None:
        self.tag = tag

    def __repr__(self) -> str:
        return f"<no value 0x{self.tag:02x}>"


@dataclass(frozen=True, slots=True)
class VarBind:
    """One OID and its decoded value."""

    oid: str
    value: object

    @property
    def is_missing(self) -> bool:
        """Whether the agent had no value for this OID."""
        return isinstance(self.value, _EndOfView)


# ---------------------------------------------------------------------------
# BER encoding


def _encode_length(length: int) -> bytes:
    if length < 0x80:
        return bytes([length])
    body = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def _tlv(tag: int, value: bytes) -> bytes:
    return bytes([tag]) + _encode_length(len(value)) + value


def _encode_integer(value: int) -> bytes:
    size = max(1, (value.bit_length() + 8) // 8)
    return _tlv(_INTEGER, value.to_bytes(size, "big", signed=True))


def _encode_oid(oid: str) -> bytes:
    parts = [int(part) for part in oid.strip(".").split(".")]
    if len(parts) < 2:
        raise ValueError(f"OID needs at least two arcs: {oid!r}")
    body = bytearray()
    # The first two arcs share one sub-identifier, which can itself exceed 127.
    for arc in [parts[0] * 40 + parts[1], *parts[2:]]:
        chunk = [arc & 0x7F]
        rest = arc >> 7
        while rest:
            chunk.append(0x80 | (rest & 0x7F))
            rest >>= 7
        body.extend(reversed(chunk))
    return _tlv(_OID, bytes(body))


def encode_request(
    pdu_type: int,
    community: str,
    request_id: int,
    oids: list[str],
    non_repeaters: int = 0,
    max_repetitions: int = 0,
) -> bytes:
    """Build a complete SNMPv2c message for a Get or GetBulk request."""
    varbinds = b"".join(_tlv(_SEQUENCE, _encode_oid(oid) + _tlv(_NULL, b"")) for oid in oids)
    pdu = _tlv(
        pdu_type,
        _encode_integer(request_id)
        + _encode_integer(non_repeaters)
        + _encode_integer(max_repetitions)
        + _tlv(_SEQUENCE, varbinds),
    )
    message = _encode_integer(_VERSION_2C) + _tlv(_OCTET_STRING, community.encode()) + pdu
    return _tlv(_SEQUENCE, message)


# ---------------------------------------------------------------------------
# BER decoding


def _read_tlv(data: bytes, pos: int) -> tuple[int, bytes, int]:
    """Return (tag, value, next position) for the element starting at ``pos``."""
    try:
        tag = data[pos]
        first = data[pos + 1]
    except IndexError as err:
        raise SnmpProtocolError("truncated element") from err
    pos += 2
    if first & 0x80:
        count = first & 0x7F
        if count == 0 or count > 4:
            raise SnmpProtocolError("unsupported length encoding")
        length = int.from_bytes(data[pos : pos + count], "big")
        pos += count
    else:
        length = first
    end = pos + length
    if end > len(data):
        raise SnmpProtocolError("element runs past the end of the message")
    return tag, data[pos:end], end


def _decode_oid(body: bytes) -> str:
    if not body:
        raise SnmpProtocolError("empty OID")
    sub_ids: list[int] = []
    value = 0
    for byte in body:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            sub_ids.append(value)
            value = 0
    first = sub_ids[0]
    top = min(first // 40, 2)
    arcs = [top, first - 40 * top, *sub_ids[1:]]
    return ".".join(str(arc) for arc in arcs)


def _decode_value(tag: int, body: bytes) -> object:
    if tag == _INTEGER:
        return int.from_bytes(body, "big", signed=True)
    if tag in _UNSIGNED_TAGS:
        return int.from_bytes(body, "big", signed=False)
    if tag in (_OCTET_STRING, _OPAQUE):
        return body
    if tag == _OID:
        return _decode_oid(body)
    if tag == _IP_ADDRESS:
        return ".".join(str(b) for b in body)
    if tag == _NULL:
        return None
    if tag in (_NO_SUCH_OBJECT, _NO_SUCH_INSTANCE, _END_OF_MIB_VIEW):
        return _EndOfView(tag)
    raise SnmpProtocolError(f"unsupported value type 0x{tag:02x}")


@dataclass(frozen=True, slots=True)
class Response:
    """A decoded GetResponse PDU."""

    request_id: int
    error_status: int
    error_index: int
    varbinds: list[VarBind]


def decode_response(data: bytes) -> Response:
    """Decode a complete SNMPv2c GetResponse message."""
    tag, message, _ = _read_tlv(data, 0)
    if tag != _SEQUENCE:
        raise SnmpProtocolError("message is not a SEQUENCE")
    tag, _version, pos = _read_tlv(message, 0)
    tag, _community, pos = _read_tlv(message, pos)
    tag, pdu, _ = _read_tlv(message, pos)
    if tag != _GET_RESPONSE:
        raise SnmpProtocolError(f"unexpected PDU type 0x{tag:02x}")
    _, request_id, pos = _read_tlv(pdu, 0)
    _, error_status, pos = _read_tlv(pdu, pos)
    _, error_index, pos = _read_tlv(pdu, pos)
    tag, varbind_list, _ = _read_tlv(pdu, pos)
    if tag != _SEQUENCE:
        raise SnmpProtocolError("varbind list is not a SEQUENCE")
    varbinds: list[VarBind] = []
    pos = 0
    while pos < len(varbind_list):
        _, varbind, pos = _read_tlv(varbind_list, pos)
        oid_tag, oid_body, inner = _read_tlv(varbind, 0)
        if oid_tag != _OID:
            raise SnmpProtocolError("varbind does not start with an OID")
        value_tag, value_body, _ = _read_tlv(varbind, inner)
        varbinds.append(VarBind(_decode_oid(oid_body), _decode_value(value_tag, value_body)))
    return Response(
        request_id=int.from_bytes(request_id, "big", signed=True),
        error_status=int.from_bytes(error_status, "big", signed=True),
        error_index=int.from_bytes(error_index, "big", signed=True),
        varbinds=varbinds,
    )


def oid_in_subtree(oid: str, base: str) -> bool:
    """Whether ``oid`` lies strictly below ``base``."""
    return oid.startswith(base.rstrip(".") + ".")


def _oid_key(oid: str) -> tuple[int, ...]:
    return tuple(int(part) for part in oid.split("."))


# ---------------------------------------------------------------------------
# Transport


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.pending: dict[int, asyncio.Future[Response]] = {}
        self.lost: Exception | None = None

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        try:
            response = decode_response(data)
        except SnmpProtocolError:
            return
        future = self.pending.pop(response.request_id, None)
        if future is not None and not future.done():
            future.set_result(response)

    def error_received(self, exc: Exception) -> None:
        for future in self.pending.values():
            if not future.done():
                future.set_exception(SnmpError(str(exc)))
        self.pending.clear()

    def connection_lost(self, exc: Exception | None) -> None:
        self.lost = exc or SnmpError("socket closed")
        for future in self.pending.values():
            if not future.done():
                future.set_exception(self.lost)
        self.pending.clear()


class SnmpClient:
    """Read-only SNMPv2c client for a single agent."""

    def __init__(
        self,
        host: str,
        community: str,
        port: int = 161,
        timeout: float = 2.0,
        retries: int = 2,
        max_repetitions: int = 40,
        concurrency: int = 1,
    ) -> None:
        self.host = host
        self.port = port
        self._community = community
        self._timeout = timeout
        self._retries = retries
        self._max_repetitions = max_repetitions
        self._semaphore = asyncio.Semaphore(concurrency)
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _Protocol | None = None
        self._connect_lock = asyncio.Lock()
        self._ids = itertools.count(random.randint(1, 0x3FFFFFFF))

    async def _ensure_socket(self) -> _Protocol:
        async with self._connect_lock:
            if self._transport is None or self._transport.is_closing():
                loop = asyncio.get_running_loop()
                self._transport, self._protocol = await loop.create_datagram_endpoint(
                    _Protocol, remote_addr=(self.host, self.port)
                )
            assert self._protocol is not None
            return self._protocol

    def close(self) -> None:
        """Close the socket. The client reopens it on the next request."""
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._protocol = None

    def _next_id(self) -> int:
        value = next(self._ids) & 0x7FFFFFFF
        return value or next(self._ids) & 0x7FFFFFFF

    async def _request(
        self, pdu_type: int, oids: list[str], non_repeaters: int = 0, max_repetitions: int = 0
    ) -> Response:
        async with self._semaphore:
            last_error: Exception | None = None
            for _attempt in range(self._retries + 1):
                protocol = await self._ensure_socket()
                request_id = self._next_id()
                future: asyncio.Future[Response] = asyncio.get_running_loop().create_future()
                protocol.pending[request_id] = future
                assert self._transport is not None
                self._transport.sendto(
                    encode_request(
                        pdu_type, self._community, request_id, oids, non_repeaters, max_repetitions
                    )
                )
                try:
                    return await asyncio.wait_for(future, self._timeout)
                except TimeoutError as err:
                    protocol.pending.pop(request_id, None)
                    last_error = err
                except SnmpError as err:
                    protocol.pending.pop(request_id, None)
                    self.close()
                    last_error = err
            if isinstance(last_error, SnmpError):
                raise last_error
            raise SnmpTimeout(f"no answer from {self.host}:{self.port}")

    async def get(self, oids: list[str]) -> dict[str, object]:
        """Fetch scalar values. OIDs the agent does not know are left out."""
        response = await self._request(_GET_REQUEST, oids)
        if response.error_status:
            raise SnmpErrorStatus(response.error_status, response.error_index)
        return {vb.oid: vb.value for vb in response.varbinds if not vb.is_missing}

    async def walk(self, base: str) -> dict[str, object]:
        """Return every value below ``base``, keyed by the OID suffix after it."""
        return (await self.walk_columns([base]))[base.strip(".")]

    async def walk_columns(self, bases: list[str]) -> dict[str, dict[str, object]]:
        """Walk several subtrees at once, typically the columns of one table.

        Each GetBulk carries one varbind per unfinished column, so a table whose
        rows fit into ``max_repetitions`` costs a single round trip instead of one
        per column. The agent answers row by row, in the order of the request.

        Returns a dict per base, keyed by the OID suffix below that base. A column
        is finished when the agent leaves its subtree, reports the end of its MIB
        view, or stops making progress.
        """
        bases = [base.strip(".") for base in bases]
        results: dict[str, dict[str, object]] = {base: {} for base in bases}
        cursors = {base: base for base in bases}
        active = list(bases)
        repetitions = self._max_repetitions
        while active:
            response = await self._request(
                _GET_BULK_REQUEST,
                [cursors[base] for base in active],
                max_repetitions=repetitions,
            )
            if response.error_status == _ERROR_TOO_BIG and repetitions > 1:
                repetitions = max(1, repetitions // 2)
                continue
            if response.error_status:
                raise SnmpErrorStatus(response.error_status, response.error_index)
            if not response.varbinds:
                break
            finished: set[str] = set()
            progressed = False
            for position, vb in enumerate(response.varbinds):
                base = active[position % len(active)]
                if base in finished:
                    continue
                if (
                    vb.is_missing
                    or not oid_in_subtree(vb.oid, base)
                    # An agent that does not advance would loop forever.
                    or _oid_key(vb.oid) <= _oid_key(cursors[base])
                ):
                    finished.add(base)
                    continue
                results[base][vb.oid[len(base) + 1 :]] = vb.value
                cursors[base] = vb.oid
                progressed = True
            # Agents may shorten a GetBulk answer. Columns that got nothing this
            # time are asked again from their cursor, unless nothing moved at all.
            if not progressed and not finished:
                break
            active = [base for base in active if base not in finished]
        return results
