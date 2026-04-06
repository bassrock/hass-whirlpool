"""Constants for the Whirlpool Washer integration."""

from __future__ import annotations

import logging

DOMAIN = "whirlpool_washer"
MANUFACTURER = "Whirlpool"
LOGGER = logging.getLogger(__name__)

# Whirlpool Cloud API
API_BASE = "https://api.whrcloud.com"
CLIENT_ID = "whirlpool_android_v2"
CLIENT_SECRET = "rMVCgnKKhIjoorcRa7cpckh5irsomybd4tM9Ir3QxJxQZlzgWSeWpkkxmsRg1PL-"

# AWS IoT
AWS_REGION = "us-east-2"
IOT_ENDPOINT = "wt.applianceconnect.net"

# Timing
POLL_INTERVAL = 300  # 5 minutes heartbeat for getState
MQTT_KEEPALIVE = 30
CONNECTION_TIMEOUT = 15.0
CREDENTIAL_REFRESH_BUFFER = 300  # Refresh AWS creds 5 min before expiry

# Config entry keys
CONF_SAID = "said"
CONF_MODEL = "model"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_ACCOUNT_ID = "account_id"

# HTTP headers (reverse-engineered from Android app)
COMMON_HEADERS = {
    "User-Agent": "okhttp/3.12.0",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

# Machine states (legacy format, kept for reference)
MACHINE_STATES = {
    0: "Standby",
    1: "Setting",
    2: "Delay Countdown",
    3: "Delay Pause",
    4: "Smart Delay",
    5: "Smart Grid Pause",
    6: "Pause",
    7: "Running Main Cycle",
    8: "Running Post Cycle",
    9: "Exceptions",
    10: "Complete",
    11: "Power Failure",
    12: "Service Diagnostic",
    13: "Factory Diagnostic",
    14: "Life Test",
    15: "Customer Focus",
    16: "Demo Mode",
    17: "Hard Stop / Error",
    18: "System Init",
}
