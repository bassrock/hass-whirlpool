"""Sensor entities for Whirlpool Washer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import WhirlpoolConfigEntry, WhirlpoolDataUpdateCoordinator
from .entity import WhirlpoolEntity


@dataclass(frozen=True, kw_only=True)
class WhirlpoolSensorEntityDescription(SensorEntityDescription):
    """Sensor entity description with value extraction function."""

    value_fn: Callable[[dict[str, Any]], StateType]


def _get_nested(data: dict, *keys, default=None):
    """Safely traverse nested dicts."""
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key, default)
    return data


SENSORS: tuple[WhirlpoolSensorEntityDescription, ...] = (
    WhirlpoolSensorEntityDescription(
        key="appliance_state",
        translation_key="appliance_state",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "running",
            "idle",
            "complete",
            "standby",
            "pause",
            "delayed",
        ],
        value_fn=lambda data: _get_nested(data, "washer", "applianceState"),
    ),
    WhirlpoolSensorEntityDescription(
        key="cycle_name",
        translation_key="cycle_name",
        value_fn=lambda data: _get_nested(data, "washer", "cycleName"),
    ),
    WhirlpoolSensorEntityDescription(
        key="current_phase",
        translation_key="current_phase",
        value_fn=lambda data: _get_nested(data, "washer", "currentPhase"),
    ),
    WhirlpoolSensorEntityDescription(
        key="time_remaining",
        translation_key="time_remaining",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        value_fn=lambda data: (
            t // 60
            if (t := _get_nested(data, "washer", "cycleTime", "time", default=0))
            else None
        ),
    ),
    WhirlpoolSensorEntityDescription(
        key="estimated_completion",
        translation_key="estimated_completion",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: (
            datetime.fromtimestamp(ts, tz=timezone.utc)
            if (ts := _get_nested(data, "washer", "cycleTime", "timeComplete", default=0))
            else None
        ),
    ),
    WhirlpoolSensorEntityDescription(
        key="system_version",
        translation_key="system_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("systemVersion"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Whirlpool Washer sensor entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        WhirlpoolSensor(coordinator, description) for description in SENSORS
    )


class WhirlpoolSensor(WhirlpoolEntity, SensorEntity):
    """Whirlpool Washer sensor entity."""

    entity_description: WhirlpoolSensorEntityDescription

    def __init__(
        self,
        coordinator: WhirlpoolDataUpdateCoordinator,
        description: WhirlpoolSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
