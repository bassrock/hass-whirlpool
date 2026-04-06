# Whirlpool Cloud API Reference

Reverse-engineered from the Android app `com.whirlpool.android.wpapp`.

Whirlpool has two generations of connected appliances:
- **Legacy (SAID)**: Uses REST API + STOMP WebSocket for state and commands
- **ThingShield (TS_SAID)**: Uses AWS IoT MQTT for state and commands

## Base URLs

| Region | REST API | MQTT Endpoint |
|--------|----------|---------------|
| US (NAR) | `https://api.whrcloud.com` | `wt.applianceconnect.net` |
| EU (EMEA) | `https://prod-api.whrcloud.eu` | `wt-eu.applianceconnect.net` |

---

## Authentication

### Step 1: OAuth2 Password Grant

```
POST /oauth/token
Content-Type: application/x-www-form-urlencoded
User-Agent: okhttp/3.12.0
```

**Form Parameters:**

| Parameter | Value |
|-----------|-------|
| `grant_type` | `password` |
| `username` | User's email |
| `password` | User's password |
| `client_id` | See [Client Credentials](#client-credentials) |
| `client_secret` | See [Client Credentials](#client-credentials) |

**Response:**

```json
{
  "access_token": "eyJ...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 21600,
  "accountId": "1234567",
  "UserName": "YourUsername",
  "SAID": [],
  "TS_SAID": ["WPXXXXXXXXXX"]
}
```

- `SAID`: Legacy appliance IDs (use REST API)
- `TS_SAID`: ThingShield appliance IDs (use AWS IoT MQTT)

### Client Credentials

| Region | Brand | Client ID | Client Secret |
|--------|-------|-----------|---------------|
| US | Whirlpool | `whirlpool_android_v2` | `rMVCgnKKhIjoorcRa7cpckh5irsomybd4tM9Ir3QxJxQZlzgWSeWpkkxmsRg1PL-` |
| US | Maytag | `maytag_android_v2` | `ULTqdvvqK0O9XcSLO3nA2tJDTLFKxdaaeKrimPYdXvnLX_yUtPhxovESldBId0Tf` |
| US | KitchenAid | `kitchenaid_android_v2` | `jd15ExiJdEt8UgLWBslwkzkQkmRGCR9lVSgeaqcPmFZQc9pgxtpjmaPSw3g-aRXG` |
| EU | Whirlpool | `whirlpool_emea_android_v2` | `90_3TBRfXfcdCYJj6L5BThEqOBZNkEchrTPT7loqm0gBS_tyeFIIEv47mmYTZkb6` |
| EU | KitchenAid | `kitchenaid_android_stg` | `Dn-ukFAFoSWOnB9nVm7Y2DDj4Gs9Bocm6aOkhy0mdNGBj5RcoLkRfCXujuxpKrqF2w15sl1tI45JXwK5Zi4saw` |

### Step 2: Cognito Identity (ThingShield only)

```
GET /api/v1/cognito/identityid
Authorization: Bearer {access_token}
```

**Response:**

```json
{
  "identityId": "us-east-2:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "token": "eyJ..."
}
```

### Step 3: AWS Credentials (ThingShield only)

Use AWS Cognito `GetCredentialsForIdentity` with anonymous credentials:

```python
cognito_client.get_credentials_for_identity(
    IdentityId="us-east-2:xxxxxxxx-...",
    Logins={"cognito-identity.amazonaws.com": token}
)
```

Returns temporary `AccessKeyId`, `SecretKey`, `SessionToken` for AWS IoT access.

**AWS Details:**
- Region: `us-east-2`
- Account: `595287146689`
- Auth Role: `iot-cf-nar-identity-pool-auth-role`

### Common Headers (REST API requests)

```
Authorization: Bearer {access_token}
Content-Type: application/json
User-Agent: okhttp/3.12.0
Pragma: no-cache
Cache-Control: no-cache
```

---

## ThingShield Appliances (AWS IoT MQTT)

Newer appliances (identified by `TS_SAID` in auth response) use AWS IoT MQTT instead of the REST API.

### MQTT Connection

Connect using `AWSIoTPythonSDK` or `awsiotsdk` with WebSocket + SigV4 auth:

```python
from awsiot import mqtt_connection_builder

connection = mqtt_connection_builder.websockets_with_default_aws_signing(
    endpoint="wt.applianceconnect.net",
    region="us-east-2",
    credentials_provider=credentials_provider,  # from Cognito
    client_id="{identity_id}_{random_suffix}",
    clean_session=True,
    keep_alive_secs=30,
)
```

**Client ID format:** `{cognitoIdentityId}_{deviceIdentifier}`
- The Android app uses `{cognitoIdentityId}_{androidId}`
- Any unique suffix works

### MQTT Topics

| Purpose | Topic Pattern | Direction |
|---------|--------------|-----------|
| Send command | `cmd/{model}/{said}/request/{clientId}` | Publish |
| Command response | `cmd/{model}/{said}/response/{clientId}` | Subscribe |
| State updates | `dt/{model}/{said}/state/update` | Subscribe |
| Presence connected | `$aws/events/presence/connected/{said}` | Subscribe |
| Presence disconnected | `$aws/events/presence/disconnected/{said}` | Subscribe |
| Capability download | `api/capability/download/{model}/{said}/response` | Subscribe |
| OTA status | `dt/{model}/{said}/ota/status` | Subscribe |

- `{model}` = thing type name (e.g. `WFW6720RW0`)
- `{said}` = thing name (e.g. `WPXXXXXXXXXX`)
- `{clientId}` = MQTT client ID used in connection

### getState Command

**Publish to:** `cmd/{model}/{said}/request/{clientId}`

```json
{
  "requestId": "uuid-v4",
  "timestamp": 1773599995000,
  "payload": {
    "addressee": "appliance",
    "command": "getState"
  }
}
```

**Response on:** `cmd/{model}/{said}/response/{clientId}`

```json
{
  "requestId": "uuid-v4",
  "response": "accepted",
  "payload": {
    "washer": {
      "applianceState": "running",
      "cycleName": "refresh",
      "cycleType": "standard",
      "specialName": "",
      "currentPhase": "spin",
      "cycleTime": {
        "state": "running",
        "time": 14357,
        "timeComplete": 1773612212,
        "timePaused": 0
      },
      "delayTime": {
        "state": "idle",
        "time": 0,
        "timeComplete": 0,
        "timePaused": 0
      },
      "sessionId": "4b13abc4-b76e-4cce-a170-a2dc6920706f",
      "cleanWasher": false,
      "doorStatus": "closed",
      "doorLockStatus": true
    },
    "remoteStartEnable": false,
    "hmiControlLockout": false,
    "faultHistory": ["none", "none", "none", "none", "none"],
    "activeFault": "none",
    "sound": {
      "cycleSignal": "min"
    },
    "capabilityPartNumber": "P0292105219",
    "systemVersion": "2.0.0"
  }
}
```

### ThingShield Washer State Fields

| Field | Description |
|-------|-------------|
| `washer.applianceState` | `"running"`, `"idle"`, `"complete"`, etc. |
| `washer.cycleName` | Current cycle: `"refresh"`, `"normal"`, `"heavy"`, etc. |
| `washer.cycleType` | `"standard"` |
| `washer.currentPhase` | `"spin"`, `"wash"`, `"rinse"`, `"fill"`, `"idle"`, etc. |
| `washer.cycleTime.state` | `"running"`, `"idle"` |
| `washer.cycleTime.time` | Remaining time in seconds |
| `washer.cycleTime.timeComplete` | Unix timestamp of estimated completion |
| `washer.delayTime.state` | `"idle"` or `"running"` |
| `washer.doorStatus` | `"closed"` or `"open"` |
| `washer.doorLockStatus` | `true` / `false` |
| `washer.cleanWasher` | Clean washer reminder |
| `remoteStartEnable` | Remote start enabled |
| `hmiControlLockout` | Control panel locked |
| `activeFault` | Current fault code or `"none"` |
| `faultHistory` | Array of last 5 fault codes |
| `sound.cycleSignal` | End-of-cycle signal volume: `"min"`, `"med"`, `"max"`, `"off"` |
| `capabilityPartNumber` | Firmware capability identifier |
| `systemVersion` | Firmware version |

### AWS IoT Thing Attributes

Accessible via `iot:DescribeThing` with Cognito credentials:

| Attribute | Example |
|-----------|---------|
| `Brand` | `WHIRLPOOL` |
| `Category` | `LAUNDRY` |
| `Serial` | `XX0000000` |
| `Name` | Hex-encoded appliance name |
| Thing Type | Model number (e.g. `WFW6720RW0`) |

---

## Legacy Appliances (REST API + STOMP WebSocket)

Older appliances (identified by `SAID` in auth response) use the REST API.

### List Owned Appliances

```
GET /api/v3/appliance/all/account/{account_id}
```

Response is a nested structure:

```json
{
  "{accountId}": {
    "{locationId}": {
      "tsAppliance": [],
      "legacyAppliance": [
        {
          "SAID": "WPR1XXXXX",
          "NAME": "My Washer",
          "DATA_MODEL": "washer_model_v1",
          "CATEGORY": "Washer"
        }
      ]
    }
  }
}
```

### List Shared Appliances

```
GET /api/v1/share-accounts/appliances
WP-CLIENT-BRAND: Whirlpool
```

### Get Appliance Data

```
GET /api/v1/appliance/{said}
```

Returns all attributes for a legacy appliance. **Does not work for TS_SAID devices** (returns 401).

### Send Command

```
POST /api/v1/appliance/command
```

```json
{
  "said": "WPR1XXXXX",
  "setAttributes": {
    "Cavity_CycleStatusMachineState": "1"
  }
}
```

### STOMP WebSocket (Legacy Real-time Updates)

1. `GET /api/v1/client_auth/webSocketUrl` → `{"url": "wss://..."}`
2. Connect WebSocket, send STOMP CONNECT with `wcloudtoken:{access_token}`
3. Subscribe to `/topic/{said}` per appliance
4. Messages arrive as:

```json
{
  "said": "WPR1XXXXX",
  "attributeMap": {
    "Cavity_CycleStatusMachineState": "7",
    "Cavity_TimeStatusEstTimeRemaining": "42"
  },
  "timestamp": 1700000000000
}
```

Send heartbeat `\n` every 30 seconds.

### Legacy Washer Attributes

#### Machine State (`Cavity_CycleStatusMachineState`)

| Value | State |
|-------|-------|
| 0 | Standby |
| 1 | Setting |
| 2 | Delay Countdown |
| 3 | Delay Pause |
| 4 | Smart Delay |
| 5 | Smart Grid Pause |
| 6 | Pause |
| 7 | Running Main Cycle |
| 8 | Running Post Cycle |
| 9 | Exceptions |
| 10 | Complete |
| 11 | Power Failure |
| 12 | Service Diagnostic |
| 13 | Factory Diagnostic |
| 14 | Life Test |
| 15 | Customer Focus |
| 16 | Demo Mode |
| 17 | Hard Stop / Error |
| 18 | System Init |

#### Cycle Status Flags (boolean: "0" or "1")

| Attribute | Description |
|-----------|-------------|
| `WashCavity_CycleStatusSensing` | Sensing load |
| `WashCavity_CycleStatusFilling` | Filling with water |
| `WashCavity_CycleStatusSoaking` | Soaking |
| `WashCavity_CycleStatusWashing` | Washing |
| `WashCavity_CycleStatusRinsing` | Rinsing |
| `WashCavity_CycleStatusSpinning` | Spinning |

#### Other Key Attributes

| Attribute | Description |
|-----------|-------------|
| `Cavity_TimeStatusEstTimeRemaining` | Estimated time remaining (minutes) |
| `Cavity_OpStatusDoorOpen` | Door open: "1", closed: "0" |
| `WashCavity_OpStatusBulkDispense1Level` | Detergent dispense level |
| `Online` | Appliance connectivity: "1" or "0" |

---

## Data.json Decryption

The MQTT endpoint and other environment configs are stored in an encrypted `Data.json` in the APK assets.

**Encryption:** AES-256-GCM
- Key: `SHA-512("Smart2000")[:16]`
- IV: 12 zero bytes
- Tag: 128 bits
- Payload: Base64-encoded

**Decrypted structure:**

```json
{
  "NAR": {
    "WHIRLPOOL": {
      "PRODUCTION": {
        "customerSpecificEndpoint": "wt.applianceconnect.net",
        "clientId": "whirlpool_android_v2",
        "clientSecret": "...",
        "baseUrl": "https://api.whrcloud.com/"
      }
    }
  },
  "EMEA": {
    "WHIRLPOOL": {
      "PRODUCTION": {
        "customerSpecificEndpoint": "wt-eu.applianceconnect.net",
        "baseUrl": "https://api.whrcloud.eu/"
      }
    }
  }
}
```
