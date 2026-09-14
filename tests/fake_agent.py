"""A minimal SNMPv2c agent on localhost, for tests.

It answers Get and GetBulk from a fixed table and can be told to misbehave:
cap the number of varbinds per answer, report tooBig above a size, or stay
silent. Values are encoded with the same BER helpers the client decodes with,
so a round trip exercises both directions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from custom_components.zyxel_switch import snmp

INTEGER = 0x02
OCTET_STRING = 0x04
OID = 0x06
COUNTER32 = 0x41
GAUGE32 = 0x42
TIMETICKS = 0x43
COUNTER64 = 0x46


def _unsigned(tag: int, value: int) -> bytes:
    body = value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big")
    return snmp._tlv(tag, body)


def encode_value(tag: int, value: object) -> bytes:
    if tag == INTEGER:
        return snmp._encode_integer(int(value))
    if tag == OCTET_STRING:
        return snmp._tlv(OCTET_STRING, value if isinstance(value, bytes) else str(value).encode())
    if tag == OID:
        return snmp._encode_oid(str(value))
    if tag in (COUNTER32, GAUGE32, TIMETICKS, COUNTER64):
        return _unsigned(tag, int(value))
    raise ValueError(tag)


def _key(oid: str) -> tuple[int, ...]:
    return tuple(int(p) for p in oid.split("."))


def _decode_request(data: bytes) -> tuple[int, str, int, int, int, list[str]]:
    _, message, _ = snmp._read_tlv(data, 0)
    _, _version, pos = snmp._read_tlv(message, 0)
    _, community, pos = snmp._read_tlv(message, pos)
    pdu_type, pdu, _ = snmp._read_tlv(message, pos)
    _, request_id, pos = snmp._read_tlv(pdu, 0)
    _, non_rep, pos = snmp._read_tlv(pdu, pos)
    _, max_rep, pos = snmp._read_tlv(pdu, pos)
    _, varbind_list, _ = snmp._read_tlv(pdu, pos)
    oids = []
    pos = 0
    while pos < len(varbind_list):
        _, vb, pos = snmp._read_tlv(varbind_list, pos)
        _, oid_body, _ = snmp._read_tlv(vb, 0)
        oids.append(snmp._decode_oid(oid_body))
    return (
        pdu_type,
        community.decode(),
        int.from_bytes(request_id, "big", signed=True),
        int.from_bytes(non_rep, "big"),
        int.from_bytes(max_rep, "big"),
        oids,
    )


@dataclass
class FakeAgent(asyncio.DatagramProtocol):
    table: dict[str, tuple[int, object]]
    community: str = "public"
    max_varbinds: int | None = None
    too_big_above: int | None = None
    silent: bool = False
    requests: list[tuple[int, list[str], int]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.reload()

    def reload(self) -> None:
        """Pick up changes made to ``table``."""
        self._sorted = sorted(self.table, key=_key)

    def connection_made(self, transport) -> None:  # noqa: ANN001
        self.transport = transport

    def _next(self, oid: str) -> str | None:
        k = _key(oid)
        for candidate in self._sorted:
            if _key(candidate) > k:
                return candidate
        return None

    def datagram_received(self, data: bytes, addr) -> None:  # noqa: ANN001
        pdu_type, community, request_id, non_rep, max_rep, oids = _decode_request(data)
        self.requests.append((pdu_type, oids, max_rep))
        if self.silent or community != self.community:
            return
        varbinds: list[bytes] = []
        if pdu_type == snmp._GET_REQUEST:
            for oid in oids:
                if oid in self.table:
                    varbinds.append(self._vb(oid))
                else:
                    varbinds.append(snmp._tlv(0x30, snmp._encode_oid(oid) + snmp._tlv(0x81, b"")))
        elif pdu_type == snmp._GET_BULK_REQUEST:
            if self.too_big_above is not None and max_rep * len(oids) > self.too_big_above:
                self._reply(addr, request_id, [], error_status=1)
                return
            cursors = list(oids)
            for _ in range(max_rep):
                for i, cursor in enumerate(cursors):
                    nxt = self._next(cursor) if cursor is not None else None
                    if nxt is None:
                        varbinds.append(
                            snmp._tlv(
                                0x30, snmp._encode_oid(cursor or oids[i]) + snmp._tlv(0x82, b"")
                            )
                        )
                    else:
                        varbinds.append(self._vb(nxt))
                    cursors[i] = nxt
            if self.max_varbinds is not None:
                varbinds = varbinds[: self.max_varbinds]
        self._reply(addr, request_id, varbinds)

    def _vb(self, oid: str) -> bytes:
        tag, value = self.table[oid]
        return snmp._tlv(0x30, snmp._encode_oid(oid) + encode_value(tag, value))

    def _reply(self, addr, request_id: int, varbinds: list[bytes], error_status: int = 0) -> None:  # noqa: ANN001
        pdu = snmp._tlv(
            snmp._GET_RESPONSE,
            snmp._encode_integer(request_id)
            + snmp._encode_integer(error_status)
            + snmp._encode_integer(0)
            + snmp._tlv(0x30, b"".join(varbinds)),
        )
        message = snmp._encode_integer(1) + snmp._tlv(OCTET_STRING, self.community.encode()) + pdu
        assert self.transport is not None
        self.transport.sendto(snmp._tlv(0x30, message), addr)


async def start_agent(agent: FakeAgent) -> tuple[asyncio.DatagramTransport, int]:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(lambda: agent, local_addr=("127.0.0.1", 0))
    return transport, transport.get_extra_info("sockname")[1]


def switch_table(
    *,
    uptime_ticks: int = 123_456_00,
    in_octets: tuple[int, ...] = (1_000_000, 0, 5_000_000, 0),
    out_octets: tuple[int, ...] = (2_000_000, 0, 1_000_000, 0),
    extra_port: bool = False,
    firmware: str = "V2.90(AAHL.2) | 05/07/2026",
) -> dict[str, tuple[int, object]]:
    """A small, made-up GS1900: four ports, one active LAG, one absent LAG, one VLAN."""
    t: dict[str, tuple[int, object]] = {
        "1.3.6.1.2.1.1.1.0": (OCTET_STRING, "GS1900-8"),
        "1.3.6.1.2.1.1.2.0": (OID, "1.3.6.1.4.1.890.1.15"),
        "1.3.6.1.2.1.1.3.0": (TIMETICKS, uptime_ticks),
        "1.3.6.1.2.1.1.5.0": (OCTET_STRING, "TestSwitch"),
        "1.3.6.1.4.1.890.1.15.3.1.6.0": (OCTET_STRING, firmware),
        "1.3.6.1.4.1.890.1.15.3.1.11.0": (OCTET_STRING, "GS1900-8"),
        "1.3.6.1.4.1.890.1.15.3.1.12.0": (OCTET_STRING, "S000TEST0001"),
    }
    ports = [
        # index, type, name, alias, oper, speed
        (1, 6, "GigabitEthernet1", "Router", 1, 1000),
        (2, 6, "GigabitEthernet2", "", 2, 0),
        (3, 6, "GigabitEthernet3", "NAS", 1, 1000),
        (4, 6, "GigabitEthernet4", "NAS", 1, 1000),
        (1000, 161, "LAG1", "NAS", 1, 1000),
        (1001, 161, "LAG2", "", 6, 0),
        (5000, 136, "VLAN1", "", 1, 1000),
    ]
    if extra_port:
        ports.append((5, 6, "GigabitEthernet5", "Spare", 1, 100))
    for n, (index, kind, name, alias, oper, speed) in enumerate(ports):
        t[f"1.3.6.1.2.1.2.2.1.3.{index}"] = (INTEGER, kind)
        t[f"1.3.6.1.2.1.2.2.1.7.{index}"] = (INTEGER, 1)
        t[f"1.3.6.1.2.1.2.2.1.8.{index}"] = (INTEGER, oper)
        t[f"1.3.6.1.2.1.2.2.1.14.{index}"] = (COUNTER32, 3 if index == 3 else 0)
        t[f"1.3.6.1.2.1.2.2.1.20.{index}"] = (COUNTER32, 0)
        t[f"1.3.6.1.2.1.31.1.1.1.1.{index}"] = (OCTET_STRING, name)
        t[f"1.3.6.1.2.1.31.1.1.1.6.{index}"] = (
            COUNTER64,
            in_octets[n] if n < len(in_octets) else 0,
        )
        t[f"1.3.6.1.2.1.31.1.1.1.10.{index}"] = (
            COUNTER64,
            out_octets[n] if n < len(out_octets) else 0,
        )
        t[f"1.3.6.1.2.1.31.1.1.1.15.{index}"] = (GAUGE32, speed)
        t[f"1.3.6.1.2.1.31.1.1.1.18.{index}"] = (OCTET_STRING, alias)
        t[f"1.3.6.1.2.1.17.1.4.1.2.{index}"] = (INTEGER, index)
    # FDB: VLAN 1, MAC as six arcs -> bridge port. Port 0 is the CPU.
    fdb = [
        ((0, 1, 2, 3, 4, 5), 1),
        ((0, 1, 2, 3, 4, 6), 1),
        ((0, 1, 2, 3, 4, 7), 1000),
        ((0, 1, 2, 3, 4, 8), 0),
    ]
    for mac, bridge_port in fdb:
        t["1.3.6.1.2.1.17.7.1.2.2.1.2.1." + ".".join(map(str, mac))] = (INTEGER, bridge_port)
    t["1.0.8802.1.1.2.1.4.1.1.9.0.1.1"] = (OCTET_STRING, "Router")
    # Something after every walked subtree, so walks must stop on their own.
    t["1.3.6.1.2.1.99.0"] = (INTEGER, 1)
    t["2.0"] = (INTEGER, 1)
    return t
