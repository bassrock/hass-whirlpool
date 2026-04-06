#!/usr/bin/env python3
"""
Whirlpool Cloud API test script.

Authenticates, discovers appliances, fetches washer status via AWS IoT MQTT,
and listens for real-time updates.

Auth flow (reverse-engineered from Android app):
  1. OAuth2 password grant → whrcloud Bearer token
  2. GET /api/v1/cognito/identityid → {identityId, token}
  3. Cognito GetCredentialsForIdentity → AWS temp credentials
  4. AWS IoT MQTT: subscribe to state topics, publish getState command

MQTT topic patterns (from decompiled APK IotClient.java):
  Command request:  cmd/{model}/{said}/request/{clientId}
  Command response: cmd/{model}/{said}/response/{identityId}
  State updates:    dt/{model}/{said}/state/update
  Presence:         $aws/events/presence/connected/{said}
                    $aws/events/presence/disconnected/{said}

Usage:
    pip install aiohttp boto3 awsiotsdk
    python test_whirlpool_status.py
"""

import asyncio
import json
import os
import sys
import time
import uuid
import threading
from pathlib import Path

import aiohttp

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

API_BASE = "https://api.whrcloud.com"
CLIENT_ID = "whirlpool_android_v2"
CLIENT_SECRET = "rMVCgnKKhIjoorcRa7cpckh5irsomybd4tM9Ir3QxJxQZlzgWSeWpkkxmsRg1PL-"

USERNAME = os.environ.get("WHIRLPOOL_USER", "")
PASSWORD = os.environ.get("WHIRLPOOL_PASS", "")

MQTT_LISTEN_SECONDS = int(os.environ.get("MQTT_LISTEN_SECONDS", "30"))
AWS_REGION = "us-east-2"
# IoT MQTT endpoint from decrypted Data.json (NAR/WHIRLPOOL/PRODUCTION)
IOT_ENDPOINT = "wt.applianceconnect.net"

OUTPUT_DIR = Path(__file__).parent / "output"

COMMON_HEADERS = {
    "User-Agent": "okhttp/3.12.0",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

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

CYCLE_FLAGS = [
    "WashCavity_CycleStatusSensing",
    "WashCavity_CycleStatusFilling",
    "WashCavity_CycleStatusSoaking",
    "WashCavity_CycleStatusWashing",
    "WashCavity_CycleStatusRinsing",
    "WashCavity_CycleStatusSpinning",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def save_json(filename: str, data):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / filename
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"  -> Saved {path}")


def print_appliance_state(data: dict):
    """Parse and display appliance state from MQTT response."""
    if not data:
        print("    No data received.")
        return

    payload = data.get("payload", data)
    response = data.get("response", "?")
    print(f"    Response          : {response}")

    # New ThingShield format: payload has "washer" key with structured state
    washer = payload.get("washer", {})
    if washer:
        state = washer.get("applianceState", "?")
        cycle = washer.get("cycleName", "?")
        cycle_type = washer.get("cycleType", "?")
        phase = washer.get("currentPhase", "?")
        door = washer.get("doorStatus", "?")
        door_lock = washer.get("doorLockStatus", "?")
        clean_washer = washer.get("cleanWasher", "?")

        cycle_time = washer.get("cycleTime", {})
        time_remaining = cycle_time.get("time", 0)
        time_complete = cycle_time.get("timeComplete", 0)
        time_state = cycle_time.get("state", "?")

        delay_time = washer.get("delayTime", {})

        print(f"    Appliance State   : {state}")
        print(f"    Cycle             : {cycle} ({cycle_type})")
        print(f"    Current Phase     : {phase}")
        print(f"    Cycle Time State  : {time_state}")
        if time_remaining:
            mins = time_remaining // 60
            secs = time_remaining % 60
            print(f"    Time Remaining    : {mins}m {secs}s ({time_remaining}s)")
        if time_complete:
            import datetime
            completion = datetime.datetime.fromtimestamp(time_complete)
            print(f"    Est. Completion   : {completion.strftime('%I:%M %p')}")
        if delay_time.get("state") != "idle":
            print(f"    Delay             : {delay_time}")
        print(f"    Door              : {door}")
        print(f"    Door Lock         : {door_lock}")
        print(f"    Clean Washer      : {clean_washer}")
    else:
        # Legacy attribute-based format
        state_raw = payload.get("Cavity_CycleStatusMachineState", "?")
        try:
            state_int = int(state_raw)
            state_name = MACHINE_STATES.get(state_int, f"Unknown({state_int})")
        except (ValueError, TypeError):
            state_name = f"Raw: {state_raw}"
        print(f"    Machine State     : {state_name} ({state_raw})")

    # Top-level fields
    remote_start = payload.get("remoteStartEnable", "?")
    hmi_lock = payload.get("hmiControlLockout", "?")
    fault = payload.get("activeFault", "?")
    fault_history = payload.get("faultHistory", [])
    sound = payload.get("sound", {})
    cap_part = payload.get("capabilityPartNumber", "?")
    sys_ver = payload.get("systemVersion", "?")

    print(f"    Remote Start      : {remote_start}")
    print(f"    HMI Lock          : {hmi_lock}")
    print(f"    Active Fault      : {fault}")
    if any(f != "none" for f in fault_history):
        print(f"    Fault History     : {fault_history}")
    print(f"    Sound Signal      : {sound.get('cycleSignal', '?')}")
    print(f"    Capability Part # : {cap_part}")
    print(f"    System Version    : {sys_ver}")


# ---------------------------------------------------------------------------
# Step 1: OAuth Authentication
# ---------------------------------------------------------------------------

async def authenticate(session: aiohttp.ClientSession) -> dict:
    print("\n=== Step 1: OAuth Authentication ===")
    url = f"{API_BASE}/oauth/token"
    data = {
        "grant_type": "password",
        "username": USERNAME,
        "password": PASSWORD,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    headers = {**COMMON_HEADERS, "Content-Type": "application/x-www-form-urlencoded"}

    async with session.post(url, data=data, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            print(f"  AUTH FAILED ({resp.status}): {body}")
            sys.exit(1)
        result = await resp.json()

    save_json("auth_raw.json", result)
    print(f"  Account ID : {result.get('accountId')}")
    print(f"  User       : {result.get('UserName')}")
    print(f"  SAID       : {result.get('SAID')}")
    print(f"  TS_SAID    : {result.get('TS_SAID')}")
    return result


# ---------------------------------------------------------------------------
# Step 2: Get Cognito Identity & AWS Credentials
# ---------------------------------------------------------------------------

async def get_cognito_identity(session: aiohttp.ClientSession, token: str) -> dict:
    """Call /api/v1/cognito/identityid to get Cognito identity ID and token."""
    print("\n=== Step 2: Cognito Identity & AWS Credentials ===")
    url = f"{API_BASE}/api/v1/cognito/identityid"
    headers = {**COMMON_HEADERS, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            body = await resp.text()
            print(f"  Failed to get Cognito identity ({resp.status}): {body}")
            return {}
        body_text = await resp.text()
        result = json.loads(body_text)

    identity_id = result.get("identityId", "")
    cognito_token = result.get("token", "")
    print(f"  Identity ID: {identity_id}")
    print(f"  Token length: {len(cognito_token)}")
    save_json("cognito_identity_raw.json", result)

    import boto3
    cognito_client = boto3.client(
        "cognito-identity",
        region_name=AWS_REGION,
        aws_access_key_id="anonymous",
        aws_secret_access_key="anonymous",
    )

    try:
        creds_response = cognito_client.get_credentials_for_identity(
            IdentityId=identity_id,
            Logins={"cognito-identity.amazonaws.com": cognito_token},
        )
        creds = creds_response["Credentials"]
        print(f"  AWS Access Key: {creds['AccessKeyId'][:10]}...")
        print(f"  Expires: {creds['Expiration']}")
        save_json("aws_credentials.json", {
            "AccessKeyId": creds["AccessKeyId"],
            "Expiration": str(creds["Expiration"]),
            "IdentityId": creds_response["IdentityId"],
        })
        return {
            "identity_id": creds_response["IdentityId"],
            "access_key": creds["AccessKeyId"],
            "secret_key": creds["SecretKey"],
            "session_token": creds["SessionToken"],
        }
    except Exception as e:
        print(f"  Cognito GetCredentialsForIdentity failed: {e}")
        return {}


# ---------------------------------------------------------------------------
# Step 3: Discover Appliances via AWS IoT
# ---------------------------------------------------------------------------

def discover_things(aws_creds: dict, ts_saids: list) -> list:
    """Discover appliances via AWS IoT DescribeThing."""
    import boto3
    print("\n=== Step 3: Discover Appliances (AWS IoT) ===")

    kwargs = {
        "region_name": AWS_REGION,
        "aws_access_key_id": aws_creds["access_key"],
        "aws_secret_access_key": aws_creds["secret_key"],
        "aws_session_token": aws_creds["session_token"],
    }
    iot_client = boto3.client("iot", **kwargs)
    things = []

    # Try thing group lookup
    identity_id = aws_creds["identity_id"]
    try:
        resp = iot_client.list_things_in_thing_group(thingGroupName=identity_id)
        group_things = resp.get("things", [])
        print(f"  Thing group '{identity_id}': {group_things}")
        for name in group_things:
            if name not in [t.get("thingName") for t in things]:
                things.append({"thingName": name, "source": "thing-group"})
    except Exception as e:
        print(f"  Thing group lookup failed: {e}")

    # Use TS_SAIDs from auth
    for said in ts_saids:
        if said not in [t.get("thingName") for t in things]:
            things.append({"thingName": said, "source": "TS_SAID"})

    # Get details for each thing
    for thing in things:
        try:
            desc = iot_client.describe_thing(thingName=thing["thingName"])
            thing["attributes"] = desc.get("attributes", {})
            thing["thingTypeName"] = desc.get("thingTypeName", "?")
            thing["thingId"] = desc.get("thingId", "?")
            print(f"\n  {thing['thingName']}:")
            print(f"    Type     : {thing['thingTypeName']}")
            print(f"    Brand    : {thing['attributes'].get('Brand', '?')}")
            print(f"    Category : {thing['attributes'].get('Category', '?')}")
            print(f"    Serial   : {thing['attributes'].get('Serial', '?')}")
            name_hex = thing['attributes'].get('Name', '')
            try:
                print(f"    Name     : {bytes.fromhex(name_hex).decode()}")
            except Exception:
                print(f"    Name     : {name_hex}")
        except Exception as e:
            print(f"  describe_thing({thing['thingName']}) failed: {e}")

    print(f"\n  IoT MQTT Endpoint: {IOT_ENDPOINT} (from decrypted Data.json)")

    save_json("things_discovered.json", things)
    return things


# ---------------------------------------------------------------------------
# Step 4: Fetch Appliance State via MQTT
# ---------------------------------------------------------------------------

def fetch_state_via_mqtt(aws_creds: dict, things: list):
    """
    Connect to AWS IoT MQTT, subscribe to response/state topics,
    publish getState command, and collect the response.

    Based on decompiled IotClient.java:
    - Subscribe: cmd/{model}/{said}/response/{identityId}
    - Subscribe: dt/{model}/{said}/state/update
    - Publish:   cmd/{model}/{said}/request/{clientId}
    - Payload:   {"requestId": uuid, "timestamp": epoch_ms,
                  "payload": {"addressee": "appliance", "command": "getState"}}
    """
    print("\n=== Step 4: Fetch Appliance State (MQTT) ===")

    if not things:
        print("  No things to query.")
        return

    from awscrt import mqtt, auth, io, http
    from awsiot import mqtt_connection_builder

    identity_id = aws_creds["identity_id"]
    # Client ID format from APK: {identityId}_{androidId}
    # We use a random suffix since we don't have an android_id
    client_id = f"{identity_id}_{uuid.uuid4().hex[:16]}"

    print(f"  Client ID: {client_id}")
    print(f"  Endpoint:  {IOT_ENDPOINT}")

    # Build MQTT connection with Cognito credentials (SigV4)
    credentials_provider = auth.AwsCredentialsProvider.new_static(
        access_key_id=aws_creds["access_key"],
        secret_access_key=aws_creds["secret_key"],
        session_token=aws_creds["session_token"],
    )

    connection = mqtt_connection_builder.websockets_with_default_aws_signing(
        endpoint=IOT_ENDPOINT,
        region=AWS_REGION,
        credentials_provider=credentials_provider,
        client_id=client_id,
        clean_session=True,
        keep_alive_secs=30,
    )

    # Track received messages
    received_states = {}
    state_events = {t["thingName"]: threading.Event() for t in things}

    def on_response(topic, payload, **kwargs):
        """Handle command response messages."""
        try:
            data = json.loads(payload)
            print(f"\n  [MQTT Response] Topic: {topic}")
            print(f"    Keys: {list(data.keys())}")
            save_json(f"mqtt_response_{int(time.time())}.json", data)

            # Extract the state from the response
            # Response format varies - may have attributes at top level or nested
            attrs = data
            if "body" in data:
                attrs = data["body"]
            if "attributes" in attrs:
                attrs = attrs["attributes"]

            for thing in things:
                if thing["thingName"] in topic:
                    received_states[thing["thingName"]] = attrs
                    state_events[thing["thingName"]].set()
                    break
        except Exception as e:
            print(f"  [MQTT Response] Parse error: {e}")
            print(f"    Raw: {payload[:500] if isinstance(payload, str) else payload.decode('utf-8', errors='replace')[:500]}")

    def on_state_update(topic, payload, **kwargs):
        """Handle state update messages."""
        try:
            data = json.loads(payload)
            print(f"\n  [MQTT State Update] Topic: {topic}")
            save_json(f"mqtt_state_update_{int(time.time())}.json", data)

            for thing in things:
                if thing["thingName"] in topic:
                    received_states[thing["thingName"]] = data
                    state_events[thing["thingName"]].set()
                    break
        except Exception as e:
            print(f"  [MQTT State Update] Parse error: {e}")

    def on_presence(topic, payload, **kwargs):
        """Handle presence events."""
        try:
            data = json.loads(payload) if payload else {}
            event_type = "connected" if "connected/" in topic else "disconnected"
            said = topic.split("/")[-1]
            print(f"  [Presence] {said}: {event_type}")
        except Exception as e:
            print(f"  [Presence] {topic}: {e}")

    # Connect
    print("\n  Connecting to AWS IoT MQTT...")
    connect_future = connection.connect()
    try:
        connect_future.result(timeout=15)
        print("  MQTT Connected!")
    except Exception as e:
        print(f"  MQTT Connection failed: {e}")
        return

    try:
        for thing in things:
            said = thing["thingName"]
            model = thing.get("thingTypeName", "unknown")

            # Subscribe to response topic (must match the client_id used in publish)
            response_topic = f"cmd/{model}/{said}/response/{client_id}"
            print(f"  Subscribing: {response_topic}")
            sub_future, _ = connection.subscribe(
                topic=response_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=on_response,
            )
            sub_future.result(timeout=10)

            # Subscribe to state update topic
            state_topic = f"dt/{model}/{said}/state/update"
            print(f"  Subscribing: {state_topic}")
            sub_future, _ = connection.subscribe(
                topic=state_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=on_state_update,
            )
            sub_future.result(timeout=10)

            # Subscribe to presence topics
            for presence_type in ["connected", "disconnected"]:
                presence_topic = f"$aws/events/presence/{presence_type}/{said}"
                print(f"  Subscribing: {presence_topic}")
                sub_future, _ = connection.subscribe(
                    topic=presence_topic,
                    qos=mqtt.QoS.AT_LEAST_ONCE,
                    callback=on_presence,
                )
                sub_future.result(timeout=10)

            # Publish getState command (Whirlpool custom MQTT protocol)
            request_topic = f"cmd/{model}/{said}/request/{client_id}"
            request_id = str(uuid.uuid4())
            payload = {
                "requestId": request_id,
                "timestamp": int(time.time() * 1000),
                "payload": {
                    "addressee": "appliance",
                    "command": "getState",
                },
            }
            print(f"\n  Publishing getState to: {request_topic}")
            print(f"    Request ID: {request_id}")
            pub_future, _ = connection.publish(
                topic=request_topic,
                payload=json.dumps(payload),
                qos=mqtt.QoS.AT_LEAST_ONCE,
            )
            pub_future.result(timeout=10)
            print("    Published!")

        # Wait for responses
        print(f"\n  Waiting for responses (up to {MQTT_LISTEN_SECONDS}s)...")
        start = time.time()
        all_received = False

        while time.time() - start < MQTT_LISTEN_SECONDS:
            all_received = all(e.is_set() for e in state_events.values())
            if all_received:
                print("  All responses received!")
                break
            time.sleep(0.5)
            elapsed = int(time.time() - start)
            if elapsed % 5 == 0 and elapsed > 0:
                pending = [s for s, e in state_events.items() if not e.is_set()]
                print(f"  [{elapsed}s] Still waiting for: {pending}")

        if not all_received:
            pending = [s for s, e in state_events.items() if not e.is_set()]
            print(f"  Timeout. No response from: {pending}")
            print("  (Appliance may be offline/unplugged — it must be connected to WiFi to respond)")

        # Display received states
        for thing in things:
            said = thing["thingName"]
            model = thing.get("thingTypeName", "?")
            print(f"\n  --- {said} ({model}) ---")

            if said in received_states:
                state_data = received_states[said]
                save_json(f"appliance_state_{said}.json", state_data)
                print_appliance_state(state_data)
            else:
                print("    No state received.")

    finally:
        print("\n  Disconnecting MQTT...")
        disconnect_future = connection.disconnect()
        try:
            disconnect_future.result(timeout=10)
            print("  MQTT Disconnected.")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("Whirlpool Cloud API Test Script")
    print("=" * 50)

    async with aiohttp.ClientSession() as session:
        # Step 1: OAuth
        auth = await authenticate(session)
        token = auth["access_token"]
        ts_saids = auth.get("TS_SAID") or []
        legacy_saids = auth.get("SAID") or []
        all_saids = ts_saids + legacy_saids

        if not all_saids:
            print("\nNo appliances (SAID or TS_SAID) in auth response. Exiting.")
            return

        # Step 2: Get AWS credentials via Cognito
        aws_creds = await get_cognito_identity(session, token)

        if aws_creds:
            # Step 3: Discover things via AWS IoT
            things = discover_things(aws_creds, ts_saids)

            # Step 4: Fetch appliance state via MQTT
            if things:
                fetch_state_via_mqtt(aws_creds, things)
        else:
            print("\n  Skipping AWS IoT steps (no credentials).")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
