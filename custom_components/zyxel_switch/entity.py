"""Base entities for the Zyxel Switch integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ZyxelSwitchCoordinator
from .device import PortState


class ZyxelSwitchEntity(CoordinatorEntity[ZyxelSwitchCoordinator]):
    """An entity that belongs to the switch device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ZyxelSwitchCoordinator, key: str) -> None:
        super().__init__(coordinator)
        info = coordinator.info
        assert info is not None
        identifier = coordinator.device_identifier
        self._attr_unique_id = f"{identifier}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identifier)},
            manufacturer="Zyxel" if info.is_zyxel else None,
            model=info.model,
            name=info.name,
            serial_number=info.serial,
            sw_version=info.firmware,
            configuration_url=f"http://{coordinator.config_entry.data[CONF_HOST]}",
        )


class ZyxelPortEntity(ZyxelSwitchEntity):
    """An entity for one interface, named after its front-panel number and alias."""

    def __init__(self, coordinator: ZyxelSwitchCoordinator, index: int, key: str) -> None:
        super().__init__(coordinator, f"if{index}_{key}")
        self._index = index
        self._attr_translation_key = key
        port = coordinator.data.state.ports[index]
        self._attr_translation_placeholders = {"port": port.label}

    @property
    def port(self) -> PortState | None:
        return self.coordinator.data.state.ports.get(self._index)

    @property
    def available(self) -> bool:
        return super().available and self._index in self.coordinator.data.state.ports
