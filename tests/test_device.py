"""Turning SNMP tables into switch info and port state."""

from __future__ import annotations

import pytest

from custom_components.zyxel_switch.device import PortState, ZyxelSwitch, rates


async def test_info(agent) -> None:  # noqa: ANN001
    _, port = agent
    switch = ZyxelSwitch("127.0.0.1", "public", port)
    try:
        info = await switch.async_get_info()
    finally:
        switch.close()
    assert info.name == "TestSwitch"
    assert info.model == "GS1900-8"
    assert info.serial == "S000TEST0001"
    assert info.firmware == "V2.90(AAHL.2)"
    assert info.is_zyxel


async def test_state(agent) -> None:  # noqa: ANN001
    _, port = agent
    switch = ZyxelSwitch("127.0.0.1", "public", port)
    try:
        state = await switch.async_get_state()
    finally:
        switch.close()

    # Ethernet ports and the present LAG; not the absent LAG, not the VLAN.
    assert list(state.ports) == [1, 2, 3, 4, 1000]
    router, idle, nas, _, lag = (state.ports[i] for i in (1, 2, 3, 4, 1000))
    assert router.label == "Port 1 (Router)"
    assert idle.label == "Port 2"
    assert lag.label == "LAG 1 (NAS)"
    assert router.is_up and not idle.is_up
    assert (router.speed_mbps, idle.speed_mbps) == (1000, 0)
    assert nas.in_errors == 3
    assert router.neighbor == "Router"
    # Two MACs on port 1, one on the LAG; the CPU entry (bridge port 0) is not counted.
    assert (router.mac_count, lag.mac_count, state.mac_count) == (2, 1, 3)
    assert state.uptime_seconds == 123_456


def _port(in_octets: int | None, out_octets: int | None) -> PortState:
    return PortState(1, "Gi1", "", False, "up", "up", 1000, in_octets, out_octets, 0, 0)


@pytest.mark.parametrize(
    ("before", "after", "elapsed", "expected"),
    [
        (_port(0, 0), _port(125_000_000, 12_500_000), 10, (100.0, 10.0)),
        (None, _port(1, 1), 10, (None, None)),
        (_port(500, 500), _port(100, 900), 10, (None, 0.00032)),
        (_port(0, 0), _port(1, 1), 0, (None, None)),
        (_port(None, 0), _port(1, 1), 10, (None, 0.0000008)),
    ],
    ids=["normal", "first-sample", "counter-reset", "no-time", "missing-counter"],
)
def test_rates(before, after, elapsed, expected) -> None:  # noqa: ANN001
    rx, tx = rates(before, after, elapsed)
    assert rx == pytest.approx(expected[0]) if expected[0] is not None else rx is None
    assert tx == pytest.approx(expected[1]) if expected[1] is not None else tx is None
