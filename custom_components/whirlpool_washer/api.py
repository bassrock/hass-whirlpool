"""API client for Whirlpool Cloud."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import time
from typing import Any, Callable
import uuid

import aiohttp

from .const import (
    API_BASE,
    AWS_REGION,
    CLIENT_ID,
    CLIENT_SECRET,
    COMMON_HEADERS,
    CONNECTION_TIMEOUT,
    CREDENTIAL_REFRESH_BUFFER,
    IOT_ENDPOINT,
    MQTT_KEEPALIVE,
)

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class WhirlpoolAuthError(Exception):
    """Authentication failed."""


class WhirlpoolApiError(Exception):
    """API communication error."""


class WhirlpoolConnectionError(WhirlpoolApiError):
    """MQTT connection error."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class AwsCredentials:
    """Temporary AWS credentials from Cognito."""

    access_key: str
    secret_key: str
    session_token: str
    expiration: float  # Unix timestamp


@dataclass
class ThingInfo:
    """AWS IoT thing metadata."""

    said: str
    model: str  # thingTypeName
    brand: str
    category: str
    serial: str
    name: str  # Decoded from hex
    thing_id: str


# ---------------------------------------------------------------------------
# Auth Client (HTTP)
# ---------------------------------------------------------------------------


class WhirlpoolAuthClient:
    """Handles OAuth and Cognito credential exchange."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_login(
        self, username: str, password: str
    ) -> dict[str, Any]:
        """OAuth2 password grant. Returns full auth response."""
        url = f"{API_BASE}/oauth/token"
        data = {
            "grant_type": "password",
            "username": username,
            "password": password,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        headers = {**COMMON_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}

        try:
            async with self._session.post(url, data=data, headers=headers) as resp:
                if resp.status == 401:
                    raise WhirlpoolAuthError("Invalid credentials")
                if resp.status != 200:
                    body = await resp.text()
                    raise WhirlpoolApiError(
                        f"OAuth failed ({resp.status}): {body}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise WhirlpoolApiError(f"Connection error: {err}") from err

    async def async_refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh OAuth token. Returns full auth response."""
        url = f"{API_BASE}/oauth/token"
        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        headers = {**COMMON_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}

        try:
            async with self._session.post(url, data=data, headers=headers) as resp:
                if resp.status in (401, 403):
                    raise WhirlpoolAuthError("Refresh token expired")
                if resp.status != 200:
                    body = await resp.text()
                    raise WhirlpoolApiError(
                        f"Token refresh failed ({resp.status}): {body}"
                    )
                return await resp.json()
        except aiohttp.ClientError as err:
            raise WhirlpoolApiError(f"Connection error: {err}") from err

    async def async_get_cognito_identity(
        self, access_token: str
    ) -> tuple[str, str]:
        """Get Cognito identity ID and token. Returns (identity_id, token)."""
        url = f"{API_BASE}/api/v1/cognito/identityid"
        headers = {
            **COMMON_HEADERS,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    raise WhirlpoolAuthError("Access token expired")
                if resp.status != 200:
                    body = await resp.text()
                    raise WhirlpoolApiError(
                        f"Cognito identity failed ({resp.status}): {body}"
                    )
                result = json.loads(await resp.text())
                return result["identityId"], result["token"]
        except aiohttp.ClientError as err:
            raise WhirlpoolApiError(f"Connection error: {err}") from err

    async def async_get_aws_credentials(
        self,
        loop: asyncio.AbstractEventLoop,
        identity_id: str,
        cognito_token: str,
    ) -> AwsCredentials:
        """Exchange Cognito token for temporary AWS credentials (runs boto3 in executor)."""

        def _get_credentials() -> AwsCredentials:
            import boto3

            client = boto3.client(
                "cognito-identity",
                region_name=AWS_REGION,
                aws_access_key_id="anonymous",
                aws_secret_access_key="anonymous",
            )
            resp = client.get_credentials_for_identity(
                IdentityId=identity_id,
                Logins={"cognito-identity.amazonaws.com": cognito_token},
            )
            creds = resp["Credentials"]
            return AwsCredentials(
                access_key=creds["AccessKeyId"],
                secret_key=creds["SecretKey"],
                session_token=creds["SessionToken"],
                expiration=creds["Expiration"].timestamp(),
            )

        try:
            return await loop.run_in_executor(None, _get_credentials)
        except Exception as err:
            raise WhirlpoolApiError(
                f"Failed to get AWS credentials: {err}"
            ) from err

    async def async_discover_thing(
        self,
        loop: asyncio.AbstractEventLoop,
        aws_creds: AwsCredentials,
        said: str,
    ) -> ThingInfo:
        """Describe an AWS IoT thing to get appliance metadata."""

        def _describe_thing() -> ThingInfo:
            import boto3

            client = boto3.client(
                "iot",
                region_name=AWS_REGION,
                aws_access_key_id=aws_creds.access_key,
                aws_secret_access_key=aws_creds.secret_key,
                aws_session_token=aws_creds.session_token,
            )
            desc = client.describe_thing(thingName=said)
            attrs = desc.get("attributes", {})
            name_hex = attrs.get("Name", "")
            try:
                name = bytes.fromhex(name_hex).decode()
            except Exception:
                name = name_hex or said

            return ThingInfo(
                said=said,
                model=desc.get("thingTypeName", "Unknown"),
                brand=attrs.get("Brand", "WHIRLPOOL"),
                category=attrs.get("Category", "LAUNDRY"),
                serial=attrs.get("Serial", ""),
                name=name,
                thing_id=desc.get("thingId", ""),
            )

        try:
            return await loop.run_in_executor(None, _describe_thing)
        except Exception as err:
            raise WhirlpoolApiError(
                f"Failed to discover thing {said}: {err}"
            ) from err


# ---------------------------------------------------------------------------
# MQTT Client (AWS IoT)
# ---------------------------------------------------------------------------


class WhirlpoolMqttClient:
    """AWS IoT MQTT client for Whirlpool appliance communication."""

    def __init__(self, on_message: Callable[[str, dict], None]) -> None:
        self._on_message = on_message
        self._connection = None
        self._connected = False
        self._connecting = False
        self._client_id: str = ""

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def client_id(self) -> str:
        return self._client_id

    async def async_connect(
        self,
        loop: asyncio.AbstractEventLoop,
        aws_creds: AwsCredentials,
        identity_id: str,
    ) -> None:
        """Connect to AWS IoT MQTT via WebSocket with SigV4."""
        if self._connecting:
            return
        if self._connected:
            return

        self._connecting = True
        try:
            self._client_id = f"{identity_id}_{uuid.uuid4().hex[:16]}"

            def _build_and_connect():
                from awscrt import auth
                from awsiot import mqtt_connection_builder

                credentials_provider = auth.AwsCredentialsProvider.new_static(
                    access_key_id=aws_creds.access_key,
                    secret_access_key=aws_creds.secret_key,
                    session_token=aws_creds.session_token,
                )

                connection = mqtt_connection_builder.websockets_with_default_aws_signing(
                    endpoint=IOT_ENDPOINT,
                    region=AWS_REGION,
                    credentials_provider=credentials_provider,
                    client_id=self._client_id,
                    clean_session=True,
                    keep_alive_secs=MQTT_KEEPALIVE,
                    on_connection_interrupted=self._on_connection_interrupted,
                    on_connection_resumed=self._on_connection_resumed,
                )

                connect_future = connection.connect()
                connect_future.result(timeout=CONNECTION_TIMEOUT)
                return connection

            self._connection = await loop.run_in_executor(
                None, _build_and_connect
            )
            self._connected = True
            _LOGGER.info("Connected to AWS IoT MQTT as %s", self._client_id)
        except Exception as err:
            self._connected = False
            raise WhirlpoolConnectionError(
                f"MQTT connection failed: {err}"
            ) from err
        finally:
            self._connecting = False

    async def async_disconnect(self, loop: asyncio.AbstractEventLoop) -> None:
        """Disconnect from AWS IoT."""
        if not self._connection:
            return
        self._connected = False
        try:
            disconnect_future = self._connection.disconnect()
            await loop.run_in_executor(
                None, lambda: disconnect_future.result(timeout=CONNECTION_TIMEOUT)
            )
            _LOGGER.info("Disconnected from AWS IoT MQTT")
        except Exception as err:
            _LOGGER.warning("Error during MQTT disconnect: %s", err)
        self._connection = None

    async def async_subscribe_appliance(
        self,
        loop: asyncio.AbstractEventLoop,
        model: str,
        said: str,
    ) -> None:
        """Subscribe to all topics for an appliance."""
        from awscrt import mqtt

        if not self._connection or not self._connected:
            raise WhirlpoolConnectionError("Not connected to MQTT")

        topics = [
            f"cmd/{model}/{said}/response/{self._client_id}",
            f"dt/{model}/{said}/state/update",
            f"$aws/events/presence/connected/{said}",
            f"$aws/events/presence/disconnected/{said}",
        ]

        for topic in topics:
            _LOGGER.debug("Subscribing to %s", topic)
            sub_future, _ = self._connection.subscribe(
                topic=topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._make_callback(topic),
            )
            await loop.run_in_executor(
                None, lambda f=sub_future: f.result(timeout=CONNECTION_TIMEOUT)
            )

    async def async_publish_get_state(
        self,
        loop: asyncio.AbstractEventLoop,
        model: str,
        said: str,
    ) -> None:
        """Publish a getState command."""
        from awscrt import mqtt

        if not self._connection or not self._connected:
            raise WhirlpoolConnectionError("Not connected to MQTT")

        request_topic = f"cmd/{model}/{said}/request/{self._client_id}"
        payload = json.dumps({
            "requestId": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "payload": {
                "addressee": "appliance",
                "command": "getState",
            },
        })

        pub_future, _ = self._connection.publish(
            topic=request_topic,
            payload=payload,
            qos=mqtt.QoS.AT_LEAST_ONCE,
        )
        await loop.run_in_executor(
            None, lambda: pub_future.result(timeout=CONNECTION_TIMEOUT)
        )
        _LOGGER.debug("Published getState to %s", request_topic)

    def _make_callback(self, subscribed_topic: str):
        """Create a per-topic MQTT message callback."""

        def callback(topic, payload, **kwargs):
            try:
                data = json.loads(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                _LOGGER.warning("Failed to parse MQTT payload on %s", topic)
                return
            _LOGGER.debug("MQTT message on %s", topic)
            self._on_message(topic, data)

        return callback

    def _on_connection_interrupted(self, connection, error, **kwargs):
        _LOGGER.warning("AWS IoT MQTT connection interrupted: %s", error)
        self._connected = False

    def _on_connection_resumed(self, connection, return_code, session_present, **kwargs):
        _LOGGER.info(
            "AWS IoT MQTT connection resumed (rc=%s, session_present=%s)",
            return_code,
            session_present,
        )
        # With clean_session=True the broker drops subscriptions, so an
        # auto-resume without session_present leaves us silently subscribed
        # to nothing. Stay "disconnected" so the heartbeat triggers a full
        # reconnect-and-resubscribe via _async_open_mqtt.
        self._connected = bool(session_present)


# ---------------------------------------------------------------------------
# API Client (Facade)
# ---------------------------------------------------------------------------


class WhirlpoolApiClient:
    """High-level client coordinating auth and MQTT."""

    def __init__(
        self,
        auth_client: WhirlpoolAuthClient,
        said: str,
        model: str,
    ) -> None:
        self._auth = auth_client
        self.said = said
        self.model = model
        self._mqtt: WhirlpoolMqttClient | None = None
        self._on_message: Callable[[str, dict], None] | None = None

        # Auth state
        self._access_token: str = ""
        self._refresh_token: str = ""
        self._token_expiry: float = 0
        self._identity_id: str = ""
        self._cognito_token: str = ""
        self._aws_creds: AwsCredentials | None = None

    @property
    def refresh_token(self) -> str:
        return self._refresh_token

    @property
    def access_token(self) -> str:
        return self._access_token

    async def async_authenticate(
        self,
        loop: asyncio.AbstractEventLoop,
        username: str,
        password: str,
    ) -> None:
        """Full authentication: OAuth → Cognito → AWS credentials."""
        auth_data = await self._auth.async_login(username, password)
        await self._process_auth_response(loop, auth_data)

    async def async_authenticate_refresh(
        self,
        loop: asyncio.AbstractEventLoop,
        refresh_token: str,
    ) -> None:
        """Authenticate using refresh token."""
        auth_data = await self._auth.async_refresh_token(refresh_token)
        await self._process_auth_response(loop, auth_data)

    async def _process_auth_response(
        self,
        loop: asyncio.AbstractEventLoop,
        auth_data: dict[str, Any],
    ) -> None:
        """Process OAuth response and get AWS credentials."""
        self._access_token = auth_data["access_token"]
        self._refresh_token = auth_data.get("refresh_token", self._refresh_token)
        self._token_expiry = time.time() + auth_data.get("expires_in", 21600)

        # Get Cognito identity
        self._identity_id, self._cognito_token = (
            await self._auth.async_get_cognito_identity(self._access_token)
        )

        # Get AWS credentials
        self._aws_creds = await self._auth.async_get_aws_credentials(
            loop, self._identity_id, self._cognito_token
        )

    async def async_ensure_credentials_valid(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Check and refresh credentials if needed."""
        now = time.time()
        creds_rotated = False

        # Check OAuth token expiry (6h)
        if now >= self._token_expiry - CREDENTIAL_REFRESH_BUFFER:
            _LOGGER.debug("OAuth token expiring, refreshing")
            try:
                auth_data = await self._auth.async_refresh_token(self._refresh_token)
                self._access_token = auth_data["access_token"]
                self._refresh_token = auth_data.get(
                    "refresh_token", self._refresh_token
                )
                self._token_expiry = now + auth_data.get("expires_in", 21600)
            except WhirlpoolAuthError:
                _LOGGER.warning("Refresh token expired, cannot refresh credentials")
                raise

        # Check AWS credentials expiry (~1h)
        if (
            self._aws_creds is None
            or now >= self._aws_creds.expiration - CREDENTIAL_REFRESH_BUFFER
        ):
            _LOGGER.debug("AWS credentials expiring, refreshing")
            # Re-fetch Cognito identity (token may have expired)
            self._identity_id, self._cognito_token = (
                await self._auth.async_get_cognito_identity(self._access_token)
            )
            self._aws_creds = await self._auth.async_get_aws_credentials(
                loop, self._identity_id, self._cognito_token
            )
            creds_rotated = True

        if creds_rotated and self._mqtt is not None:
            _LOGGER.info("Reconnecting MQTT with refreshed AWS credentials")
            await self._async_open_mqtt(loop)

    async def async_connect_and_subscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        on_message: Callable[[str, dict], None],
    ) -> None:
        """Connect MQTT and subscribe for the configured appliance."""
        self._on_message = on_message
        await self._async_open_mqtt(loop)

    async def async_ensure_connected(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Reconnect MQTT from scratch if it isn't currently connected."""
        if self._mqtt is not None and self._mqtt.is_connected:
            return
        _LOGGER.info("MQTT not connected, performing full reconnect")
        await self._async_open_mqtt(loop)

    async def _async_open_mqtt(self, loop: asyncio.AbstractEventLoop) -> None:
        """Tear down any existing MQTT client and open a fresh subscribed one."""
        if self._aws_creds is None:
            raise WhirlpoolApiError("Not authenticated")
        if self._on_message is None:
            raise WhirlpoolApiError("No message handler registered")

        if self._mqtt is not None:
            try:
                await self._mqtt.async_disconnect(loop)
            except Exception as err:  # noqa: BLE001 - teardown is best-effort
                _LOGGER.debug("Ignoring error during MQTT teardown: %s", err)

        self._mqtt = WhirlpoolMqttClient(self._on_message)
        await self._mqtt.async_connect(loop, self._aws_creds, self._identity_id)
        await self._mqtt.async_subscribe_appliance(loop, self.model, self.said)

    async def async_request_state(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Publish a getState command."""
        if self._mqtt is None or not self._mqtt.is_connected:
            raise WhirlpoolConnectionError("MQTT not connected")
        await self._mqtt.async_publish_get_state(loop, self.model, self.said)

    async def async_disconnect(
        self, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Disconnect MQTT."""
        if self._mqtt is not None:
            await self._mqtt.async_disconnect(loop)
            self._mqtt = None
