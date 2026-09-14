"""Diagnostics for the Zyxel Switch integration, with the community redacted."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_COMMUNITY
from .coordinator import ZyxelSwitchConfigEntry

TO_REDACT = {CONF_COMMUNITY, "serial"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ZyxelSwitchConfigEntry
) -> dict[str, Any]:
    """Return what the integration sees, without the community or serial number."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "info": async_redact_data(asdict(coordinator.info), TO_REDACT)
        if coordinator.info
        else None,
        "uptime_seconds": data.state.uptime_seconds if data else None,
        "mac_count": data.state.mac_count if data else None,
        "ports": [
            {
                **asdict(port),
                "rx_mbps": data.rx_mbps.get(index),
                "tx_mbps": data.tx_mbps.get(index),
            }
            for index, port in data.state.ports.items()
        ]
        if data
        else [],
    }
