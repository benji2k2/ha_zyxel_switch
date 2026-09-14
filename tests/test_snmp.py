"""The SNMP client: encoding, decoding and walking against a fake agent."""

from __future__ import annotations

import pytest

from custom_components.zyxel_switch import snmp

from .fake_agent import COUNTER64, INTEGER, OCTET_STRING, FakeAgent, start_agent


@pytest.mark.parametrize(
    "oid",
    [
        "1.3.6.1.2.1.1.1.0",
        "1.0.8802.1.1.2.1.4.1.1.9.0.1.1",
        "1.3.6.1.4.1.890.268435455.127.128",
        "2.999.3",
    ],
)
def test_oid_round_trip(oid: str) -> None:
    _, body, _ = snmp._read_tlv(snmp._encode_oid(oid), 0)
    assert snmp._decode_oid(body) == oid


@pytest.mark.parametrize("value", [0, 1, 127, 128, 255, 256, -1, -128, -129, 2**31 - 1, -(2**31)])
def test_integer_round_trip(value: int) -> None:
    tag, body, _ = snmp._read_tlv(snmp._encode_integer(value), 0)
    assert snmp._decode_value(tag, body) == value


def test_unsigned_types_are_not_negative() -> None:
    assert snmp._decode_value(COUNTER64, b"\xff" * 8) == 2**64 - 1
    assert snmp._decode_value(0x41, b"\x80\x00\x00\x00") == 2**31


def test_long_length_form() -> None:
    payload = b"x" * 300
    tag, body, end = snmp._read_tlv(snmp._tlv(OCTET_STRING, payload), 0)
    assert (tag, body, end) == (OCTET_STRING, payload, 304)


def test_truncated_message_is_rejected() -> None:
    message = snmp.encode_request(snmp._GET_REQUEST, "public", 7, ["1.3.6.1.2.1.1.1.0"])
    with pytest.raises(snmp.SnmpProtocolError):
        snmp.decode_response(message[:-3])


def _table(n: int) -> dict[str, tuple[int, object]]:
    table: dict[str, tuple[int, object]] = {}
    for i in range(1, n + 1):
        table[f"1.3.6.1.2.1.2.2.1.3.{i}"] = (INTEGER, 6)
        table[f"1.3.6.1.2.1.2.2.1.8.{i}"] = (INTEGER, 1 if i % 2 else 2)
        table[f"1.3.6.1.2.1.31.1.1.1.18.{i}"] = (OCTET_STRING, f"port{i}")
    table["1.3.6.1.2.1.31.1.1.1.19.1"] = (INTEGER, 0)  # right after the alias column
    return table


async def test_get_skips_unknown_oids() -> None:
    agent = FakeAgent(_table(2))
    transport, port = await start_agent(agent)
    client = snmp.SnmpClient("127.0.0.1", "public", port)
    try:
        values = await client.get(["1.3.6.1.2.1.2.2.1.3.1", "1.3.6.1.2.1.2.2.1.3.99"])
    finally:
        client.close()
        transport.close()
    assert values == {"1.3.6.1.2.1.2.2.1.3.1": 6}


@pytest.mark.parametrize(
    ("max_repetitions", "max_varbinds", "too_big_above"),
    [(40, None, None), (3, None, None), (40, 5, None), (40, None, 20)],
    ids=["one-shot", "paged", "truncated", "too-big"],
)
async def test_walk_columns(
    max_repetitions: int, max_varbinds: int | None, too_big_above: int | None
) -> None:
    agent = FakeAgent(_table(30), max_varbinds=max_varbinds, too_big_above=too_big_above)
    transport, port = await start_agent(agent)
    client = snmp.SnmpClient("127.0.0.1", "public", port, max_repetitions=max_repetitions)
    columns = ["1.3.6.1.2.1.2.2.1.3", "1.3.6.1.2.1.2.2.1.8", "1.3.6.1.2.1.31.1.1.1.18"]
    try:
        result = await client.walk_columns(columns)
    finally:
        client.close()
        transport.close()
    assert [len(result[c]) for c in columns] == [30, 30, 30]
    assert result["1.3.6.1.2.1.31.1.1.1.18"]["17"] == b"port17"
    assert result["1.3.6.1.2.1.2.2.1.8"]["2"] == 2
    if max_repetitions == 40 and max_varbinds is None and too_big_above is None:
        assert len(agent.requests) == 1


async def test_walk_stops_at_end_of_view() -> None:
    table = {"1.3.6.1.2.1.2.2.1.3.1": (INTEGER, 6), "1.3.6.1.2.1.2.2.1.3.2": (INTEGER, 6)}
    agent = FakeAgent(table)
    transport, port = await start_agent(agent)
    client = snmp.SnmpClient("127.0.0.1", "public", port)
    try:
        assert await client.walk("1.3.6.1.2.1.2.2.1.3") == {"1": 6, "2": 6}
        assert await client.walk("1.3.6.1.9") == {}
    finally:
        client.close()
        transport.close()


async def test_timeout_after_retries() -> None:
    agent = FakeAgent(_table(1), silent=True)
    transport, port = await start_agent(agent)
    client = snmp.SnmpClient("127.0.0.1", "public", port, timeout=0.05, retries=2)
    try:
        with pytest.raises(snmp.SnmpTimeout):
            await client.get(["1.3.6.1.2.1.2.2.1.3.1"])
    finally:
        client.close()
        transport.close()
    assert len(agent.requests) == 3


async def test_wrong_community_times_out() -> None:
    agent = FakeAgent(_table(1), community="secret")
    transport, port = await start_agent(agent)
    client = snmp.SnmpClient("127.0.0.1", "public", port, timeout=0.05, retries=0)
    try:
        with pytest.raises(snmp.SnmpTimeout):
            await client.get(["1.3.6.1.2.1.2.2.1.3.1"])
    finally:
        client.close()
        transport.close()
