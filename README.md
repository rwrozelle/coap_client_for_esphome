# CoAP Client for ESPHome

A Home Assistant custom integration that connects to ESPHome devices running the [`coap_server`](https://github.com/rwrozelle/coap_client_for_esphome) external component. Communication uses the CoAP protocol over Thread (OpenThread) networks, with state updates delivered via CoAP Observe (push) — no polling.

## Prerequisites

Your ESPHome device must be on a Thread network and configured with the `coap_server` external component. The device must be reachable from the Home Assistant host via its Thread IPv6 address.

### Minimum ESPHome configuration

```yaml
external_components:
  - source: github://rwrozelle/coap_client_for_esphome@main
    components: [coap_server]

openthread:
  # ... your Thread network credentials

coap_server:
```

### Full coap_server options

```yaml
coap_server:
  port: 5683                        # UDP port (default 5683)
  server_ping_interval: 60s         # How often the device pings HA (default 60s, min 20s)
  server_ping_timeout_ratio: 2.5    # Timeout = interval × ratio (default 2.5)
  server_ping_retry: 1              # Consecutive missed pings before reconnect (default 1)
  client_ping_interval: 60s         # How often HA pings the device (default 60s)
  client_ping_timeout_ratio: 2.5
  client_ping_retry: 1
  max_connections: 1                # Max simultaneous HA connections (default 1)
  subscription_confirm: false       # Require CON observe subscriptions (default false)
  oscore:                           # Optional OSCORE encryption
    master_secret: "deadbeef..."
    master_salt: ""                 # Optional
    sender_id: "01"                 # Device's sender ID
    recipient_id: "02"              # HA client's sender ID
    id_context: ""                  # Optional
```

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to **Integrations** → **⋮** → **Custom repositories**.
3. Add `https://github.com/rwrozelle/coap_client_for_esphome` as an **Integration**.
4. Search for **CoAP Client for ESPHome** and install it.
5. Restart Home Assistant.

### Manual

1. Copy the `custom_components/coap_client_for_esphome` folder into your `<config>/custom_components/` directory.
2. Restart Home Assistant.

## Configuration

### Automatic discovery

The integration listens for `_coap._udp.local.` mDNS announcements. When a compatible device is found, a discovery notification appears in **Settings → Devices & Services**. Confirm to add it.

### Manual setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **CoAP Client for ESPHome**.
3. Enter the device's IPv6 address and port (default `5683`).

### OSCORE encryption

If the device requires OSCORE, a second step appears after the initial connection test. Enter the hex credentials from your ESPHome `coap_server` config:

| Field | ESPHome key | Notes |
|---|---|---|
| Master Secret | `master_secret` | Required |
| Master Salt | `master_salt` | Optional, leave blank if unset |
| Sender ID | `sender_id` | **The device's** sender ID |
| Recipient ID | `recipient_id` | **The device's** recipient ID (your client ID) |
| ID Context | `id_context` | Optional, leave blank if unset |

> Note: Sender ID and Recipient ID are from the **device's** perspective, matching the ESPHome config directly.

## Supported entity types

The integration creates entities for every resource advertised in the device's `.well-known/core` response. Supported resource types:

| Platform | ESPHome component |
|---|---|
| `sensor` | `sensor`, `text_sensor` |
| `binary_sensor` | `binary_sensor` |
| `switch` | `switch` |
| `button` | `button` |
| `number` | `number` |
| `lock` | `lock` |
| `valve` | `valve` |

Entity names, units, and device classes are taken from the resource attributes advertised by the device. Sub-devices (multiple physical devices on one ESPHome node) are supported via the `dv=` resource attribute.

## Options

After setup, configure options via **Settings → Devices & Services → CoAP Client for ESPHome → Configure**.

### Subscribe to logs from device

When enabled, the integration observes the device's log resource and forwards every log message into the Home Assistant logs panel. Messages appear under the logger `custom_components.coap_client_for_esphome.coordinator` prefixed with the device name:

```
custom_components.coap_client_for_esphome.coordinator - my_device: [wifi] Connected to AP
```

Log levels from ESPHome are mapped to Home Assistant log levels:

| ESPHome level | HA level |
|---|---|
| ERROR | `error` |
| WARN | `warning` |
| INFO | `info` |
| DEBUG | `debug` |
| VERBOSE | `debug` |

To control verbosity, add to your `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.coap_client_for_esphome.coordinator: debug
```

Enabling or disabling this option reloads the integration. When disabled, a CoAP observe cancellation is sent to the device so it stops buffering log entries.

## Connectivity and reconnection

The integration maintains the connection through a ping/keepalive mechanism:

- **HA → device ping**: HA periodically sends a NON GET to `/ping` on the device. The device responds with its current uptime.
- **Device → HA ping**: The device sends a NON GET to HA's `/ping` endpoint at its configured interval.
- **Reboot detection**: If the device's reported uptime decreases between pings, a reboot is detected and all observations are re-established immediately.
- **mDNS reconnect**: When a device re-announces on mDNS after a reboot, the integration reconnects without waiting for the next ping cycle.
- **Backoff**: If the device stops responding, the integration enters an exponential backoff reconnect loop (starting at 10 s, capping at 5 min).
- **Resource change detection**: If the set of entities on the device changes after a reconnect (firmware update added or removed components), the config entry reloads automatically to pick up the new entity set.

## Troubleshooting

**Device not discovered automatically**
- Confirm the Thread network is operational and the device has joined it.
- Verify the device is advertising `_coap._udp.local.` — check with `avahi-browse -r _coap._udp` or a mDNS browser.
- Try adding the device manually using its IPv6 address.

**Cannot connect / setup fails**
- Ping the device's IPv6 address from the HA host: `ping6 <device-ipv6>`.
- Confirm `port` in your ESPHome config matches what you entered (default `5683`).
- If OSCORE is enabled on the device, ensure you enter credentials — the integration detects the requirement from the `/info` endpoint.

**Entities become unavailable**
- Check the HA logs for ping timeout or backoff messages under `custom_components.coap_client_for_esphome`.
- The device may have rebooted; the integration will reconnect automatically.
- Verify the Thread network has not partitioned.

**OSCORE errors**
- Double-check that Sender ID and Recipient ID are entered from the **device's** perspective (matching the ESPHome `coap_server` config directly).
- Ensure the hex strings have no typos; each must be an even number of hex digits.

**Log messages not appearing**
- Confirm **Subscribe to logs from device** is enabled in the integration options.
- Ensure the device has `USE_LOGGER` compiled in (the default for ESPHome).
- Set the HA logger level for `custom_components.coap_client_for_esphome.coordinator` to `debug` to see all device log levels.
