"""Data update coordinator for Whirlpool Washer."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    WhirlpoolApiClient,
    WhirlpoolApiError,
    WhirlpoolAuthError,
)
from .const import (
    CONF_MODEL,
    CONF_SAID,
    DOMAIN,
    LOGGER,
    MANUFACTURER,
    POLL_INTERVAL,
)

type WhirlpoolConfigEntry = ConfigEntry[WhirlpoolDataUpdateCoordinator]


class WhirlpoolDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manage fetching Whirlpool washer state via MQTT with periodic heartbeat."""

    config_entry: WhirlpoolConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: WhirlpoolConfigEntry,
        api: WhirlpoolApiClient,
    ) -> None:
        super().__init__(
            hass,
            LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self.api = api
        self.said = config_entry.data[CONF_SAID]
        self.model = config_entry.data[CONF_MODEL]
        self._current_state: dict[str, Any] = {}
        self._appliance_online: bool = True
        self._thing_info = None

    @property
    def appliance_online(self) -> bool:
        """Whether the appliance itself is reachable (per AWS IoT presence)."""
        return self._appliance_online

    async def async_setup(self) -> None:
        """Connect MQTT and subscribe for state updates."""
        loop = asyncio.get_event_loop()
        await self.api.async_connect_and_subscribe(
            loop, self._handle_mqtt_message
        )
        # Discover thing metadata for device_info
        from .api import WhirlpoolAuthClient

        try:
            self._thing_info = await self.api._auth.async_discover_thing(
                loop, self.api._aws_creds, self.said
            )
        except Exception:
            LOGGER.debug("Could not discover thing metadata for %s", self.said)

        # Request initial state
        await self.api.async_request_state(loop)

    async def async_shutdown(self) -> None:
        """Disconnect MQTT on unload."""
        loop = asyncio.get_event_loop()
        await self.api.async_disconnect(loop)

    async def _async_update_data(self) -> dict[str, Any]:
        """Heartbeat: ensure credentials + MQTT are healthy, then poll state."""
        loop = asyncio.get_event_loop()
        try:
            await self.api.async_ensure_credentials_valid(loop)
            await self.api.async_ensure_connected(loop)
            await self.api.async_request_state(loop)
        except WhirlpoolAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except WhirlpoolApiError as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err
        return self._current_state

    def _handle_mqtt_message(self, topic: str, data: dict) -> None:
        """Handle incoming MQTT messages (called from awscrt thread)."""
        if "$aws/events/presence/connected/" in topic:
            self.hass.loop.call_soon_threadsafe(self._handle_presence_connected)
            return
        if "$aws/events/presence/disconnected/" in topic:
            self.hass.loop.call_soon_threadsafe(self._handle_presence_disconnected)
            return

        # State response or update — extract payload
        payload = data.get("payload", data)
        if isinstance(payload, dict) and "washer" in payload:
            self.hass.loop.call_soon_threadsafe(self._handle_state_update, payload)

    def _handle_state_update(self, payload: dict[str, Any]) -> None:
        """Apply a state push on the HA event loop."""
        self._current_state = payload
        # Receiving state proves the appliance is online, even if we never
        # saw the matching presence/connected event (e.g. on HA startup).
        if not self._appliance_online:
            LOGGER.info("Appliance %s back online (state received)", self.said)
            self._appliance_online = True
        self.async_set_updated_data(payload)

    def _handle_presence_connected(self) -> None:
        """Mark appliance online and pull a fresh state snapshot."""
        LOGGER.info("Appliance %s presence: connected", self.said)
        self._appliance_online = True
        self.async_update_listeners()
        self.hass.async_create_task(self._async_request_state_safe())

    def _handle_presence_disconnected(self) -> None:
        """Mark appliance offline so entities go unavailable."""
        LOGGER.info("Appliance %s presence: disconnected", self.said)
        self._appliance_online = False
        self.async_update_listeners()

    async def _async_request_state_safe(self) -> None:
        """Best-effort getState; log but don't crash if it fails."""
        try:
            await self.api.async_request_state(asyncio.get_event_loop())
        except Exception as err:  # noqa: BLE001 - best-effort refresh
            LOGGER.debug("getState after presence event failed: %s", err)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        info = self._thing_info
        name = info.name if info else f"Whirlpool Washer {self.said}"
        model = info.model if info else self.model
        serial = info.serial if info else None
        sw_version = (self._current_state or {}).get("systemVersion")

        return DeviceInfo(
            identifiers={(DOMAIN, self.said)},
            name=name,
            manufacturer=MANUFACTURER,
            model=model,
            serial_number=serial,
            sw_version=sw_version,
        )
