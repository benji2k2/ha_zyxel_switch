"""Zyxel Switch — port status and traffic over SNMP."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant

from .const import CONF_COMMUNITY, DEFAULT_PORT
from .coordinator import ZyxelSwitchConfigEntry, ZyxelSwitchCoordinator
from .device import ZyxelSwitch

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ZyxelSwitchConfigEntry) -> bool:
    """Set up a switch from a config entry."""
    client = ZyxelSwitch(
        entry.data[CONF_HOST],
        entry.data[CONF_COMMUNITY],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
    )
    coordinator = ZyxelSwitchCoordinator(hass, entry, client)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        client.close()
        raise
    coordinator.port_indexes = set(coordinator.data.state.ports)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ZyxelSwitchConfigEntry) -> None:
    """Apply a changed polling interval by reloading."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ZyxelSwitchConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.client.close()
    return unloaded
