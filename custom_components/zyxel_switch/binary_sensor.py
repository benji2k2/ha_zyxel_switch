"""Link state per interface."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import ZyxelSwitchConfigEntry, ZyxelSwitchCoordinator
from .entity import ZyxelPortEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZyxelSwitchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one link sensor per interface."""
    coordinator = entry.runtime_data
    async_add_entities(PortLinkSensor(coordinator, index) for index in coordinator.data.state.ports)


class PortLinkSensor(ZyxelPortEntity, BinarySensorEntity):
    """On while the interface is operationally up."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: ZyxelSwitchCoordinator, index: int) -> None:
        super().__init__(coordinator, index, "link")

    @property
    def is_on(self) -> bool | None:
        port = self.port
        return None if port is None else port.is_up

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        port = self.port
        if port is None:
            return None
        return {
            "interface": port.name,
            "description": port.alias or None,
            "admin_status": port.admin,
            "oper_status": port.oper,
            "speed_mbps": port.speed_mbps,
            "mac_addresses": port.mac_count,
            "lldp_neighbor": port.neighbor,
        }
