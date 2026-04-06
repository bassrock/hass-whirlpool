"""The Whirlpool Washer integration."""

from __future__ import annotations

import asyncio

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import WhirlpoolApiClient, WhirlpoolApiError, WhirlpoolAuthClient, WhirlpoolAuthError
from .const import CONF_MODEL, CONF_REFRESH_TOKEN, CONF_SAID, LOGGER
from .coordinator import WhirlpoolConfigEntry, WhirlpoolDataUpdateCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WhirlpoolConfigEntry) -> bool:
    """Set up Whirlpool Washer from a config entry."""
    session = async_get_clientsession(hass)
    auth_client = WhirlpoolAuthClient(session)
    loop = asyncio.get_event_loop()

    said = entry.data[CONF_SAID]
    model = entry.data[CONF_MODEL]
    api = WhirlpoolApiClient(auth_client, said, model)

    # Authenticate: try refresh token first, fall back to password
    refresh_token = entry.data.get(CONF_REFRESH_TOKEN)
    if refresh_token:
        try:
            await api.async_authenticate_refresh(loop, refresh_token)
            # Update stored refresh token if it changed
            if api.refresh_token != refresh_token:
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, CONF_REFRESH_TOKEN: api.refresh_token},
                )
        except WhirlpoolAuthError:
            LOGGER.debug("Refresh token expired, trying username/password")
            refresh_token = None

    if not refresh_token:
        username = entry.data.get(CONF_USERNAME)
        password = entry.data.get(CONF_PASSWORD)
        if not username or not password:
            raise ConfigEntryAuthFailed("No valid authentication credentials")
        try:
            await api.async_authenticate(loop, username, password)
            hass.config_entries.async_update_entry(
                entry,
                data={**entry.data, CONF_REFRESH_TOKEN: api.refresh_token},
            )
        except WhirlpoolAuthError as err:
            raise ConfigEntryAuthFailed("Authentication failed") from err
        except WhirlpoolApiError as err:
            raise ConfigEntryNotReady(f"Failed to connect: {err}") from err

    # Create coordinator and connect MQTT
    coordinator = WhirlpoolDataUpdateCoordinator(hass, entry, api)
    entry.runtime_data = coordinator

    try:
        await coordinator.async_setup()
    except WhirlpoolApiError as err:
        raise ConfigEntryNotReady(f"Failed to connect MQTT: {err}") from err

    await coordinator.async_config_entry_first_refresh()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: WhirlpoolConfigEntry) -> bool:
    """Unload a config entry."""
    result = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if result:
        await entry.runtime_data.async_shutdown()
    return result
