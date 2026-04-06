"""Base entity for Whirlpool Washer."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import WhirlpoolDataUpdateCoordinator


class WhirlpoolEntity(CoordinatorEntity[WhirlpoolDataUpdateCoordinator]):
    """Base entity for Whirlpool Washer entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WhirlpoolDataUpdateCoordinator,
        unique_id_suffix: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.said}_{unique_id_suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return self.coordinator.device_info
