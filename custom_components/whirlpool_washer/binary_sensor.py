"""Binary sensor entities for Whirlpool Washer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import WhirlpoolConfigEntry, WhirlpoolDataUpdateCoordinator
from .entity import WhirlpoolEntity


def _get_nested(data: dict, *keys, default=None):
    """Safely traverse nested dicts."""
    for key in keys:
        if not isinstance(data, dict):
            return default
        data = data.get(key, default)
    return data


@dataclass(frozen=True, kw_only=True)
class WhirlpoolBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Binary sensor entity description with value extraction function."""

    value_fn: Callable[[dict[str, Any]], bool | None]


BINARY_SENSORS: tuple[WhirlpoolBinarySensorEntityDescription, ...] = (
    WhirlpoolBinarySensorEntityDescription(
        key="door",
        translation_key="door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda data: _get_nested(data, "washer", "doorStatus") == "open",
    ),
    WhirlpoolBinarySensorEntityDescription(
        key="door_lock",
        translation_key="door_lock",
        device_class=BinarySensorDeviceClass.LOCK,
        value_fn=lambda data: _get_nested(data, "washer", "doorLockStatus"),
    ),
    WhirlpoolBinarySensorEntityDescription(
        key="remote_start",
        translation_key="remote_start",
        value_fn=lambda data: data.get("remoteStartEnable"),
    ),
    WhirlpoolBinarySensorEntityDescription(
        key="fault_active",
        translation_key="fault_active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda data: (
            data.get("activeFault", "none") != "none"
            if data.get("activeFault") is not None
            else None
        ),
    ),
    WhirlpoolBinarySensorEntityDescription(
        key="clean_washer",
        translation_key="clean_washer",
        value_fn=lambda data: _get_nested(data, "washer", "cleanWasher"),
    ),
    WhirlpoolBinarySensorEntityDescription(
        key="control_lock",
        translation_key="control_lock",
        device_class=BinarySensorDeviceClass.LOCK,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.get("hmiControlLockout"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WhirlpoolConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Whirlpool Washer binary sensor entities."""
    coordinator = entry.runtime_data
    async_add_entities(
        WhirlpoolBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class WhirlpoolBinarySensor(WhirlpoolEntity, BinarySensorEntity):
    """Whirlpool Washer binary sensor entity."""

    entity_description: WhirlpoolBinarySensorEntityDescription

    def __init__(
        self,
        coordinator: WhirlpoolDataUpdateCoordinator,
        description: WhirlpoolBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
