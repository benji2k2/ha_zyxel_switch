"""Speed, traffic and counters per interface, plus a few switch-wide values."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfDataRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SwitchData, ZyxelSwitchConfigEntry, ZyxelSwitchCoordinator
from .entity import ZyxelPortEntity, ZyxelSwitchEntity


@dataclass(frozen=True, kw_only=True)
class PortSensorDescription(SensorEntityDescription):
    """How to read one per-port value."""

    value_fn: Callable[[SwitchData, int], float | int | str | None]


@dataclass(frozen=True, kw_only=True)
class SwitchSensorDescription(SensorEntityDescription):
    """How to read one switch-wide value."""

    value_fn: Callable[[SwitchData], float | int | datetime | None]


def _port_attr(name: str) -> Callable[[SwitchData, int], int | str | None]:
    def read(data: SwitchData, index: int) -> int | str | None:
        port = data.state.ports.get(index)
        return None if port is None else getattr(port, name)

    return read


PORT_SENSORS: tuple[PortSensorDescription, ...] = (
    PortSensorDescription(
        key="speed",
        translation_key="speed",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_port_attr("speed_mbps"),
    ),
    PortSensorDescription(
        key="rx",
        translation_key="rx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data, index: data.rx_mbps.get(index),
    ),
    PortSensorDescription(
        key="tx",
        translation_key="tx",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda data, index: data.tx_mbps.get(index),
    ),
    PortSensorDescription(
        key="in_errors",
        translation_key="in_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_port_attr("in_errors"),
    ),
    PortSensorDescription(
        key="out_errors",
        translation_key="out_errors",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_port_attr("out_errors"),
    ),
    PortSensorDescription(
        key="mac_count",
        translation_key="mac_count",
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=_port_attr("mac_count"),
    ),
    PortSensorDescription(
        key="neighbor",
        translation_key="neighbor",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_port_attr("neighbor"),
    ),
)

SWITCH_SENSORS: tuple[SwitchSensorDescription, ...] = (
    SwitchSensorDescription(
        key="ports_up",
        translation_key="ports_up",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sum(
            1 for port in data.state.ports.values() if port.is_up and not port.is_lag
        ),
    ),
    SwitchSensorDescription(
        key="mac_count",
        translation_key="switch_mac_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.state.mac_count,
    ),
    SwitchSensorDescription(
        key="boot_time",
        translation_key="boot_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.boot_time,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ZyxelSwitchConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        SwitchSensor(coordinator, description) for description in SWITCH_SENSORS
    ]
    entities.extend(
        PortSensor(coordinator, index, description)
        for index in coordinator.data.state.ports
        for description in PORT_SENSORS
    )
    async_add_entities(entities)


class PortSensor(ZyxelPortEntity, SensorEntity):
    """A value of one interface."""

    entity_description: PortSensorDescription

    def __init__(
        self,
        coordinator: ZyxelSwitchCoordinator,
        index: int,
        description: PortSensorDescription,
    ) -> None:
        super().__init__(coordinator, index, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | str | None:
        return self.entity_description.value_fn(self.coordinator.data, self._index)


class SwitchSensor(ZyxelSwitchEntity, SensorEntity):
    """A switch-wide value."""

    entity_description: SwitchSensorDescription

    def __init__(
        self, coordinator: ZyxelSwitchCoordinator, description: SwitchSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | int | datetime | None:
        return self.entity_description.value_fn(self.coordinator.data)
