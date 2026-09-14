"""Polling for the Zyxel Switch integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DOMAIN
from .device import SwitchInfo, SwitchState, ZyxelSwitch, rates
from .snmp import SnmpError

_LOGGER = logging.getLogger(__name__)

# A reboot time that moves by less than this is jitter from polling, not a reboot.
_BOOT_TIME_TOLERANCE = timedelta(seconds=60)


@dataclass(slots=True)
class SwitchData:
    """One poll, plus what was derived from comparing it with the last one."""

    state: SwitchState
    rx_mbps: dict[int, float | None] = field(default_factory=dict)
    tx_mbps: dict[int, float | None] = field(default_factory=dict)
    boot_time: datetime | None = None


type ZyxelSwitchConfigEntry = ConfigEntry[ZyxelSwitchCoordinator]


class ZyxelSwitchCoordinator(DataUpdateCoordinator[SwitchData]):
    """Polls the switch and keeps its identity current."""

    config_entry: ZyxelSwitchConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ZyxelSwitchConfigEntry, client: ZyxelSwitch
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self.info: SwitchInfo | None = None
        self.port_indexes: set[int] = set()

    async def _async_setup(self) -> None:
        try:
            self.info = await self.client.async_get_info()
        except SnmpError as err:
            raise UpdateFailed(f"Could not read switch information: {err}") from err

    async def _async_update_data(self) -> SwitchData:
        try:
            state = await self.client.async_get_state()
        except SnmpError as err:
            raise UpdateFailed(f"Could not poll the switch: {err}") from err

        previous = self.data
        data = SwitchData(state=state)
        elapsed = state.monotonic - previous.state.monotonic if previous else 0.0
        for index, port in state.ports.items():
            before = previous.state.ports.get(index) if previous else None
            data.rx_mbps[index], data.tx_mbps[index] = rates(before, port, elapsed)

        if state.uptime_seconds is not None:
            boot_time = dt_util.utcnow() - timedelta(seconds=state.uptime_seconds)
            known = previous.boot_time if previous else None
            if known is not None and abs(boot_time - known) < _BOOT_TIME_TOLERANCE:
                boot_time = known
            elif known is not None:
                # The switch restarted; firmware may have changed with it.
                await self._async_refresh_info()
            data.boot_time = boot_time

        if previous is not None and set(state.ports) != self.port_indexes:
            _LOGGER.info("Interfaces on %s changed, reloading", self.config_entry.title)
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return data

    async def _async_refresh_info(self) -> None:
        try:
            info = await self.client.async_get_info()
        except SnmpError:
            return
        self.info = info
        self._async_update_device(info)

    @callback
    def _async_update_device(self, info: SwitchInfo) -> None:
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, self.device_identifier)})
        if device is not None:
            registry.async_update_device(
                device.id, sw_version=info.firmware, model=info.model, serial_number=info.serial
            )

    @property
    def device_identifier(self) -> str:
        """Stable identifier for the device registry."""
        return self.config_entry.unique_id or self.config_entry.entry_id
