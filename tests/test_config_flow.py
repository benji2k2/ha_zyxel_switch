"""Setting up, reconfiguring and tuning a switch through the UI."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.zyxel_switch.const import CONF_COMMUNITY, CONF_SCAN_INTERVAL, DOMAIN

from .fake_agent import FakeAgent, start_agent, switch_table


async def test_user_flow_creates_entry(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    _, port = agent
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_HOST: " 127.0.0.1 ", CONF_COMMUNITY: "public", CONF_PORT: float(port)},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "TestSwitch"
    assert result["data"] == {CONF_HOST: "127.0.0.1", CONF_COMMUNITY: "public", CONF_PORT: port}
    assert result["result"].unique_id == "S000TEST0001"


async def test_user_flow_wrong_community(hass: HomeAssistant, agent, monkeypatch) -> None:  # noqa: ANN001
    _, port = agent
    monkeypatch.setattr(
        "custom_components.zyxel_switch.snmp.SnmpClient.__init__.__defaults__",
        (161, 0.05, 0, 40, 1),
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_COMMUNITY: "wrong", CONF_PORT: port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_user_flow_already_configured(hass: HomeAssistant, agent) -> None:  # noqa: ANN001
    _, port = agent
    MockConfigEntry(
        domain=DOMAIN, unique_id="S000TEST0001", data={CONF_HOST: "10.0.0.9"}
    ).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_COMMUNITY: "public", CONF_PORT: port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_rejects_other_switch(hass: HomeAssistant) -> None:
    other = FakeAgent(switch_table())
    other.table["1.3.6.1.4.1.890.1.15.3.1.12.0"] = (0x04, "S999OTHER")
    other.reload()
    transport, port = await start_agent(other)
    try:
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id="S000TEST0001",
            data={CONF_HOST: "10.0.0.9", CONF_COMMUNITY: "public", CONF_PORT: 161},
        )
        entry.add_to_hass(hass)
        result = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_COMMUNITY: "public", CONF_PORT: port}
        )
    finally:
        transport.close()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_switch"


async def test_options_flow(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: "10.0.0.9", CONF_COMMUNITY: "public", CONF_PORT: 161}
    )
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 60.0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {CONF_SCAN_INTERVAL: 60}
