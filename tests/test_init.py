"""End to end: a config entry against the fake agent, through Home Assistant."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PORT, STATE_OFF, STATE_ON, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zyxel_switch.const import CONF_COMMUNITY, DOMAIN

from .fake_agent import switch_table


def _entry(port: int) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="S000TEST0001",
        title="TestSwitch",
        data={CONF_HOST: "127.0.0.1", CONF_COMMUNITY: "public", CONF_PORT: port},
    )


def _device(hass: HomeAssistant, entry: MockConfigEntry) -> dr.DeviceEntry:
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1
    assert (DOMAIN, "S000TEST0001") in devices[0].identifiers
    return devices[0]


def _entity_id(hass: HomeAssistant, domain: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"S000TEST0001_{key}")
    assert entity_id, key
    return entity_id


async def test_setup_creates_device_and_entities(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    fake, port = agent
    entry = _entry(port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    device = _device(hass, entry)
    assert (device.manufacturer, device.model, device.sw_version) == (
        "Zyxel",
        "GS1900-8",
        "V2.90(AAHL.2)",
    )

    link = hass.states.get(_entity_id(hass, "binary_sensor", "if1_link"))
    assert link.state == STATE_ON
    assert link.attributes["lldp_neighbor"] == "Router"
    assert link.attributes["friendly_name"] == "TestSwitch Port 1 (Router) link"
    assert hass.states.get(_entity_id(hass, "binary_sensor", "if2_link")).state == STATE_OFF
    assert hass.states.get(_entity_id(hass, "sensor", "if1_speed")).state == "1000"
    # No previous sample yet, so no rate.
    assert hass.states.get(_entity_id(hass, "sensor", "if1_rx")).state == STATE_UNKNOWN
    assert hass.states.get(_entity_id(hass, "sensor", "ports_up")).state == "3"
    assert hass.states.get(_entity_id(hass, "sensor", "mac_count")).state == "3"

    # Diagnostic extras exist but stay disabled until someone wants them.
    registry = er.async_get(hass)
    assert registry.async_get(_entity_id(hass, "sensor", "if3_in_errors")).disabled_by is not None
    assert not hass.states.async_entity_ids("switch")

    # Second poll: 10 MB more received on port 1 gives a rate.
    table = switch_table(in_octets=(11_000_000, 0, 5_000_000, 0))
    fake.table.update(table)
    fake.reload()
    coordinator = entry.runtime_data
    coordinator.data.state.monotonic -= 10  # pretend the last poll was ten seconds ago
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert float(hass.states.get(_entity_id(hass, "sensor", "if1_rx")).state) == pytest.approx(
        8.0, rel=0.02
    )

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retries_when_switch_is_silent(hass: HomeAssistant, agent, monkeypatch) -> None:  # noqa: ANN001
    fake, port = agent
    fake.silent = True
    monkeypatch.setattr(
        "custom_components.zyxel_switch.snmp.SnmpClient.__init__.__defaults__",
        (161, 0.05, 0, 40, 1),
    )
    entry = _entry(port)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_reboot_refreshes_firmware(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    fake, port = agent
    entry = _entry(port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    fake.table.update(switch_table(uptime_ticks=500, firmware="V2.91(AAHL.1) | 01/01/2027"))
    fake.reload()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    device = _device(hass, entry)
    assert device.sw_version == "V2.91(AAHL.1)"


async def test_new_interface_reloads_entry(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    fake, port = agent
    entry = _entry(port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert not er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, "S000TEST0001_if5_link"
    )

    fake.table.update(switch_table(extra_port=True))
    fake.reload()
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert er.async_get(hass).async_get_entity_id("binary_sensor", DOMAIN, "S000TEST0001_if5_link")


async def test_diagnostics_hide_community(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    from custom_components.zyxel_switch.diagnostics import async_get_config_entry_diagnostics

    _, port = agent
    entry = _entry(port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry_data"][CONF_COMMUNITY] == "**REDACTED**"
    assert diag["info"]["serial"] == "**REDACTED**"
    assert len(diag["ports"]) == 5
