# Whirlpool Washer for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

Custom Home Assistant integration for Whirlpool washers using the reverse-engineered ThingShield AWS IoT MQTT protocol.

## Features

- Real-time washer state via AWS IoT MQTT push
- Automatic credential refresh (OAuth + Cognito)
- Config flow with multi-appliance support

### Sensors
| Entity | Description |
|--------|-------------|
| State | Appliance state (running, idle, complete, standby, etc.) |
| Cycle | Current cycle name (normal, heavy, refresh, etc.) |
| Phase | Current phase (wash, rinse, spin, fill, idle) |
| Time remaining | Cycle time remaining in minutes |
| Estimated completion | Estimated completion timestamp |
| Firmware | System firmware version |

### Binary Sensors
| Entity | Description |
|--------|-------------|
| Door | Door open/closed |
| Door lock | Door lock engaged |
| Remote start | Remote start enabled |
| Fault | Active fault detected |
| Clean washer reminder | Clean washer cycle needed |
| Control lock | HMI control panel locked |

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Click the three dots menu in the top right
3. Select **Custom repositories**
4. Add `https://github.com/bassrock/hass-whirlpool` with category **Integration**
5. Click **Install**
6. Restart Home Assistant

### Manual

Copy the `custom_components/whirlpool_washer` directory to your Home Assistant `config/custom_components/` directory and restart.

## Setup

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for **Whirlpool Washer**
3. Enter your Whirlpool app email and password
4. If you have multiple appliances, select the one to add

## Requirements

- A Whirlpool washer with WiFi (ThingShield/TS_SAID type)
- A Whirlpool app account with the washer registered
