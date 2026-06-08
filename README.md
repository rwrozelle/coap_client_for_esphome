# CoAP Client for ESPHome

A Home Assistant custom integration that connects to ESPHome devices running the [`coap_server`](https://github.com/rwrozelle/esphome/tree/coap_server) external component. Communication uses the CoAP protocol over IPv6, with state updates delivered via CoAP Observe (push) — no polling.

The `coap_server` component currently runs over Thread (OpenThread). The HA integration only needs a routable IPv6 address to reach the device — Thread is a firmware-side transport concern. Devices are reachable via a Thread border router that advertises a routed prefix, or directly if the HA host is on the same Thread network.

This integration was vibe coded using Claude.

## Prerequisites

Your ESPHome device must be configured with the `coap_server` external component and reachable from the Home Assistant host via an IPv6 address. The current `coap_server` implementation uses Thread (OpenThread) as its network transport, so a Thread border router with a routed prefix is the typical setup.

### Minimum ESPHome configuration

```yaml
external_components:
  - source: github://rwrozelle/esphome@coap_server
    components: [coap_server, mdns]

openthread:
  # ... your Thread network credentials

coap_server:
```

### Full coap_server options

`subscription_confirm` selects the observe mode and determines which other options are valid.

#### NON observe mode (default — `subscription_confirm: false`)

The client sends non-confirmable (NON) observe requests. Liveness is tracked via a mutual ping mechanism.

```yaml
coap_server:
  port: 5683                        # UDP port (default 5683)
  max_connections: 1                # Max simultaneous HA connections (default 1)
  subscription_confirm: false       # NON observe mode (default)
  server_ping_interval: 60s         # How often the device pings HA (default 60s, min 20s)
  server_ping_timeout_ratio: 2.5    # Timeout = interval × ratio (default 2.5); floor of 1 second
  server_ping_retry: 1              # Consecutive missed pings before reconnect (default 1)
  client_ping_interval: 60s         # How often HA pings the device (default 60s)
  client_ping_timeout_ratio: 2.5    # Timeout = interval × ratio (default 2.5); floor of 1 second
  client_ping_retry: 1              # Consecutive missed pings before reconnect (default 1)
  oscore:                           # Optional OSCORE encryption
    master_secret: "deadbeef..."
    master_salt: ""                 # Optional
    sender_id: "01"                 # Device's sender ID
    recipient_id: "02"              # HA client's sender ID
    id_context: ""                  # Optional
```

#### CON observe mode (`subscription_confirm: true`)

The client sends confirmable (CON) observe requests. The server acknowledges each notification, providing built-in delivery confirmation. Ping is not used in this mode; `observe_retry` controls re-subscription on stream failure.

```yaml
coap_server:
  port: 5683                        # UDP port (default 5683)
  max_connections: 1                # Max simultaneous HA connections (default 1)
  subscription_confirm: true        # CON observe mode
  observe_retry: 1                  # Re-subscribe attempts after stream ends (default 1, max 10)
  oscore:                           # Optional OSCORE encryption
    master_secret: "deadbeef..."
    master_salt: ""                 # Optional
    sender_id: "01"                 # Device's sender ID
    recipient_id: "02"              # HA client's sender ID
    id_context: ""                  # Optional
```

`observe_retry` sets how many times HA will attempt to re-establish a broken observation before entering backoff reconnect. Retries use exponential backoff starting at 10 s, capping at 5 min. Setting it to `0` means the first stream failure immediately enters backoff.

> **Limitations of CON observe mode:** Reboot detection relies on the CON ACK timeout — the integration only discovers a reboot when the server tries to send a CON notification and receives no ACK. For entities that rarely change state (e.g. a switch that stays off for hours), no CON exchange occurs and a reboot may go undetected indefinitely. **Always include at least one regularly-updating entity** (such as an uptime sensor) in your CON-mode configuration so that a reboot is caught within one update cycle. mDNS re-announcements are ignored in CON mode.

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

The integration listens for `_esphome-coap-server._udp.local.` mDNS announcements. When a compatible device is found, a discovery notification appears in **Settings → Devices & Services**. Confirm to add it.

### Manual setup

1. Go to **Settings → Devices & Services → Add Integration**.
2. Search for **CoAP Client for ESPHome**.
3. Enter the device's IPv6 address and port (default `5683`).

### OSCORE encryption

The integration automatically detects whether OSCORE is required by reading the `oscore` field from the device's `/info` endpoint. When `oscore:` is present in the ESPHome `coap_server` config, the device advertises `"oscore": true` and the setup flow adds a credential step. If `oscore:` is absent, setup completes without asking for credentials.

If the device requires OSCORE, a second step appears after the initial connection test. Enter the hex credentials from your ESPHome `coap_server` config:

| Field | ESPHome key | Notes |
|---|---|---|
| Master Secret | `master_secret` | Required. Minimum 16 bytes (32 hex characters). |
| Master Salt | `master_salt` | Optional, leave blank if unset |
| Sender ID | `recipient_id` | **Your** (HA client) sender ID — the device's `recipient_id` |
| Recipient ID | `sender_id` | **The device's** sender ID |
| ID Context | `id_context` | Optional, leave blank if unset |

> Note: Sender ID and Recipient ID are **swapped** relative to the ESPHome config — each party's sender ID is the other's recipient ID. Enter the device's `recipient_id` as the HA Sender ID, and the device's `sender_id` as the HA Recipient ID.

### OSCORE replay window

OSCORE uses two independent sequence number counters to prevent replayed messages. Understanding how they persist helps diagnose authentication failures after reboots or reflashes.

#### The two counters

| Counter | Incremented by | Checked by | Persisted |
|---|---|---|---|
| **Client sender seq** | HA on every outbound request | Device's receiver window | HA config entry (threshold checkpoints every 1024) |
| **Device sender seq** | Device on every outbound notification | HA's receiver window | Device NVS (threshold checkpoints every 1024) |

Both receiver windows are **in-memory only** — they reset to empty whenever their owner restarts.

#### On HA reload or restart

When HA reloads or restarts the integration, the new coordinator:
- Reads the last saved **client sender seq threshold** from the config entry and resumes sending from there (skipping ahead to the next checkpoint boundary, at most 1024 messages)
- Creates a **fresh empty receiver window** — accepts any sequence number from the device, including one that is lower than the last seen value

This means an HA restart automatically resolves any client-side replay rejection, including the case where the device was also reflashed.

#### Device reboot and reflash scenarios

| Event | Effect on HA | Problem? |
|---|---|---|
| Device reboots (NVS intact) | Device sender seq continues from NVS; HA's fresh window on next HA restart accepts any seq | None |
| Device reflashed (NVS cleared), HA also restarted | HA receiver window is empty → accepts device seq starting at 0 | None |
| Device reflashed (NVS cleared), **HA still running** | HA receiver window still holds old seq values → **rejects device seq 0 as replay** | **Use Reconfigure → Reset replay window** |

The only scenario requiring action is a device reflash while HA keeps running. In that case, use **Reconfigure → Reset replay window** to clear HA's in-memory receiver window.

### Updating OSCORE credentials

To change OSCORE credentials (e.g. after generating new keys) or reset the replay window, go to **Settings → Devices & Services → CoAP Client for ESPHome → ⋮ → Reconfigure**. The reconfigure form pre-fills with the current credentials so you only need to change what has changed. Saving triggers an integration reload with the new credentials.

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

Entity names, units, and device classes are taken from the resource attributes advertised by the device. Sub-devices (multiple physical devices on one ESPHome node) are supported via the `dv=` resource attribute. Area assignment is also automatic: when the device's `/info` response includes an `areas` list and a sub-device references an area by index, the integration sets `suggested_area` in HA's device registry so the device appears in the correct room without manual assignment.

#### Lock entity states

The `lock` platform maps all 8 ESPHome `LockState` values to the corresponding Home Assistant lock attributes:

| ESPHome value | State | HA attribute set |
|---|---|---|
| 0 | NONE | `is_locked = None` (unknown) |
| 1 | LOCKED | `is_locked = True` |
| 2 | UNLOCKED | `is_locked = False` |
| 3 | JAMMED | `is_jammed = True` |
| 4 | LOCKING | `is_locking = True` |
| 5 | UNLOCKING | `is_unlocking = True` |
| 6 | OPENING | `is_opening = True` |
| 7 | OPEN | `is_open = True` |

States are encoded by the device as a CBOR unsigned integer (`{2: uint}`) and delivered via CoAP Observe.

The `number` platform reads `min=`, `max=`, and `step=` attributes from the device's `.well-known/core` response and applies them to the HA slider range. Requires ESPHome `coap_server` firmware built after this feature was added; older firmware falls back to a default range of 0–100 with step 1.

### Resource attributes

The integration reads the following RFC 6690 link-format attributes from each resource entry in `.well-known/core`:

| Attribute | Meaning | Used for |
|---|---|---|
| `rt=` | Resource type (e.g. `esphome.sensor`) | Determines the HA platform |
| `obs` | Observable flag | Whether to start a CoAP Observe stream |
| `oid=` | Object ID / entity name | Unique identifier and display name |
| `title=` | Human-readable title | Fallback name when `oid=` is absent |
| `uom=` | Unit of measurement | Sensor unit (e.g. `°C`, `%`) |
| `dc=` | Device class | HA device class (e.g. `temperature`, `motion`) |
| `dv=` | Device index | Sub-device assignment (1-based) |
| `stp=` | Stop path | Alternative POST path for valve stop action |
| `ct=` | Content type | CBOR = 60 (default) |

Unknown attributes are silently ignored, so firmware can include additional attributes for future use without breaking the integration.

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

CoAP notifications are sent over UDP, which is best-effort and connectionless. An individual notification may be
lost in transit — this is normal and by design. The next state change on the device delivers the current value,
so the integration self-heals automatically. No intermediate states are buffered or retried on the device side;
this is a deliberate trade-off to avoid runtime heap allocation and to allow the device to sleep between updates.

### NON observe mode (`subscription_confirm: false`)

Liveness is tracked via a mutual ping/keepalive mechanism:

- **HA → device ping**: HA periodically sends a NON GET to `/ping` on the device. The device responds with its current uptime.
- **Device → HA ping**: The device sends a NON GET to HA's `/ping` endpoint at its configured interval.
- **Reboot detection**: If the device's reported uptime decreases between pings, a reboot is detected and all observations are re-established immediately.
- **mDNS reconnect**: When a device re-announces on mDNS after a reboot, the integration reconnects without waiting for the next ping cycle.
- **Backoff**: If the device stops responding, the integration enters an exponential backoff reconnect loop (starting at 10 s, capping at 5 min).
- **Resource change detection**: If the set of entities on the device changes after a reconnect (firmware update added or removed components), the config entry reloads automatically to pick up the new entity set.

### CON observe mode (`subscription_confirm: true`)

CON notifications are acknowledged by the server, so the protocol itself confirms delivery. The ping loop is not used in this mode. Instead, the integration monitors the observation stream:

- **Stream end / failure**: If the server stops ACKing CON notifications (e.g. on reboot), aiocoap terminates the observation stream and the integration re-subscribes automatically.
- **Retry with backoff**: Re-subscription attempts use exponential backoff (starting at 10 s, capping at 5 min), up to the `observe_retry` limit.
- **Backoff reconnect after retries exhausted**: If all retry attempts fail, the device is marked unavailable and the integration enters exponential backoff reconnect — the same mechanism used in NON mode after missed pings.
- **mDNS re-announcements ignored**: In CON mode, periodic mDNS TTL re-announcements do not trigger reconnects; the CON ACK mechanism is the authoritative liveness signal.
- **Reboot detection requires regular updates**: A reboot is only detected when a CON notification is attempted and ACK'd. Entities that rarely change state will not trigger detection. Always include a regularly-updating entity (e.g. uptime sensor) to ensure reboots are caught promptly.

### Periodic resubscription

Over long runtimes (days), the aiocoap observe state for an individual resource can silently stop delivering notifications while the overall connection remains healthy — the ping still succeeds but state updates stop arriving for that entity.

To guard against this, the integration automatically re-sends `GET+Observe=0` for every resource once per day. Each resource's timer is independently jittered (±25 % of the interval) so resubscriptions are spread out rather than all firing at once. On each resubscription the device responds with the current state, so entities get a fresh value regardless of whether the stream was actually broken.

This mechanism applies in both NON and CON observe modes. In CON mode, planned resubscriptions do not consume the `observe_retry` budget.

### Refresh subscriptions button

Every device gets a **Refresh subscriptions** button entity added automatically by the integration (it does not need to be configured in ESPHome). Pressing it immediately cancels and re-establishes all observe streams for that device — equivalent to what the periodic timer does, but on demand.

Use this button when an entity appears stale and you do not want to wait for the next automatic resubscription cycle. Wire it to a physical button via an automation, add it to a dashboard, or call it from a script. The ping loop and zeroconf listener are not affected.

## Troubleshooting

**Device not discovered automatically**
- Confirm the Thread network is operational and the device has joined it.
- Verify the device is advertising `_esphome-coap-server._udp.local.` — check with `avahi-browse -r _esphome-coap-server._udp` or a mDNS browser.
- Try adding the device manually using its IPv6 address.

**Cannot connect / setup fails**
- Ping the device's IPv6 address from the HA host: `ping6 <device-ipv6>`.
- Confirm `port` in your ESPHome config matches what you entered (default `5683`).
- If OSCORE is enabled on the device, ensure you enter credentials — the integration detects the requirement from the `/info` endpoint.

**Entities become unavailable**
- Check the HA logs for ping timeout or backoff messages under `custom_components.coap_client_for_esphome`.
- The device may have rebooted; the integration will reconnect automatically.
- Verify the Thread network has not partitioned and the border router is routing the device's prefix.

**OSCORE errors**
- Double-check that Sender ID and Recipient ID are **swapped** relative to the ESPHome `coap_server` config: HA's Sender ID = device's `recipient_id`, and HA's Recipient ID = device's `sender_id`.
- Ensure the hex strings have no typos; each must be an even number of hex digits. Master Secret must be at least 32 hex characters (16 bytes).

**One entity is stale but others are updating normally**
- An individual observe stream may have silently broken. Press the **Refresh subscriptions** button on the device page to re-establish all streams immediately. The integration also does this automatically once per day.

**Log messages not appearing**
- Confirm **Subscribe to logs from device** is enabled in the integration options.
- Ensure the device has `USE_LOGGER` compiled in (the default for ESPHome).
- Set the HA logger level for `custom_components.coap_client_for_esphome.coordinator` to `debug` to see all device log levels.

## Development

### Setup

Run the setup script once after cloning. It creates `venv/` in the project root containing all test dependencies, the Home Assistant package (for hassfest/HACS validation), and pre-commit, then wires the git hooks:

```bash
scripts/setup
```

[`uv`](https://github.com/astral-sh/uv) is used for fast installs and is downloaded automatically if not present.

### Running tests

```bash
venv/bin/pytest tests/
```

Or activate the venv first:

```bash
source venv/bin/activate
pytest tests/
```

Tests use `pytest-asyncio` in auto mode. The test suite does not require a real CoAP device — all network interaction goes through an in-process mock server built on `aiocoap`.

### Pre-commit hooks

The setup script installs two git hooks that run automatically on `git commit`:

- **hassfest** — validates the integration manifest and schema against the Home Assistant core (requires `~/dev/core` to be present)
- **HACS** — validates HACS metadata

Run them manually at any time:

```bash
venv/bin/pre-commit run --all-files
```
