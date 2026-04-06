"""Config flow for Whirlpool Washer."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ThingInfo,
    WhirlpoolApiError,
    WhirlpoolAuthClient,
    WhirlpoolAuthError,
)
from .const import CONF_ACCOUNT_ID, CONF_MODEL, CONF_REFRESH_TOKEN, CONF_SAID, DOMAIN


class WhirlpoolWasherConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Whirlpool Washer."""

    VERSION = 1

    def __init__(self) -> None:
        self._auth_data: dict[str, Any] = {}
        self._things: dict[str, ThingInfo] = {}
        self._username: str = ""
        self._password: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user credentials step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]

            session = async_get_clientsession(self.hass)
            auth_client = WhirlpoolAuthClient(session)

            try:
                self._auth_data = await auth_client.async_login(
                    self._username, self._password
                )
            except WhirlpoolAuthError:
                errors["base"] = "invalid_auth"
            except WhirlpoolApiError:
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                ts_saids = self._auth_data.get("TS_SAID") or []

                if not ts_saids:
                    errors["base"] = "cannot_connect"
                elif len(ts_saids) == 1:
                    # Single appliance — try to discover metadata and create entry
                    said = ts_saids[0]
                    await self.async_set_unique_id(said)
                    self._abort_if_unique_id_configured()

                    model = await self._discover_model(auth_client, said)
                    return self.async_create_entry(
                        title=f"Whirlpool Washer ({said})",
                        data={
                            CONF_USERNAME: self._username,
                            CONF_PASSWORD: self._password,
                            CONF_REFRESH_TOKEN: self._auth_data.get("refresh_token", ""),
                            CONF_SAID: said,
                            CONF_MODEL: model,
                            CONF_ACCOUNT_ID: self._auth_data.get("accountId", ""),
                        },
                    )
                else:
                    # Multiple appliances — discover all and show selection
                    await self._discover_all_things(auth_client, ts_saids)
                    return await self.async_step_device()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle appliance selection step."""
        if user_input is not None:
            said = user_input[CONF_SAID]
            await self.async_set_unique_id(said)
            self._abort_if_unique_id_configured()

            thing = self._things.get(said)
            model = thing.model if thing else "Unknown"
            title = thing.name if thing else said

            return self.async_create_entry(
                title=f"Whirlpool {title}",
                data={
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_REFRESH_TOKEN: self._auth_data.get("refresh_token", ""),
                    CONF_SAID: said,
                    CONF_MODEL: model,
                    CONF_ACCOUNT_ID: self._auth_data.get("accountId", ""),
                },
            )

        # Build options with friendly names
        options = {}
        for said, thing in self._things.items():
            label = f"{thing.name} ({thing.model})" if thing.name != said else said
            options[said] = label

        return self.async_show_form(
            step_id="device",
            data_schema=vol.Schema(
                {vol.Required(CONF_SAID): vol.In(options)}
            ),
        )

    async def _discover_model(
        self, auth_client: WhirlpoolAuthClient, said: str
    ) -> str:
        """Discover the model name for a single SAID."""
        try:
            loop = asyncio.get_event_loop()
            identity_id, cognito_token = await auth_client.async_get_cognito_identity(
                self._auth_data["access_token"]
            )
            aws_creds = await auth_client.async_get_aws_credentials(
                loop, identity_id, cognito_token
            )
            thing = await auth_client.async_discover_thing(loop, aws_creds, said)
            return thing.model
        except Exception:
            return "Unknown"

    async def _discover_all_things(
        self, auth_client: WhirlpoolAuthClient, saids: list[str]
    ) -> None:
        """Discover metadata for all appliances."""
        try:
            loop = asyncio.get_event_loop()
            identity_id, cognito_token = await auth_client.async_get_cognito_identity(
                self._auth_data["access_token"]
            )
            aws_creds = await auth_client.async_get_aws_credentials(
                loop, identity_id, cognito_token
            )
            for said in saids:
                try:
                    thing = await auth_client.async_discover_thing(
                        loop, aws_creds, said
                    )
                    self._things[said] = thing
                except Exception:
                    self._things[said] = ThingInfo(
                        said=said,
                        model="Unknown",
                        brand="WHIRLPOOL",
                        category="LAUNDRY",
                        serial="",
                        name=said,
                        thing_id="",
                    )
        except Exception:
            # If discovery fails, just use SAIDs as-is
            for said in saids:
                self._things[said] = ThingInfo(
                    said=said,
                    model="Unknown",
                    brand="WHIRLPOOL",
                    category="LAUNDRY",
                    serial="",
                    name=said,
                    thing_id="",
                )
