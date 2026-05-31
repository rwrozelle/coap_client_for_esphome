"""Shared fixtures and mock infrastructure for coordinator tests.

HA module stubs are installed at module level so they exist in sys.modules
before coordinator.py is first imported.
"""

import asyncio
import socket
import sys
import types
from enum import Enum
from unittest.mock import AsyncMock

import aiocoap
import aiocoap.resource as resource
import cbor2
import pytest

# ---------------------------------------------------------------------------
# Stub homeassistant modules — must happen before coordinator import
# ---------------------------------------------------------------------------

_ha_stub = types.ModuleType("homeassistant")

_ha_core = types.ModuleType("homeassistant.core")
_ha_core.HomeAssistant = object
_ha_core.callback = lambda f: f

_ha_config_entries = types.ModuleType("homeassistant.config_entries")


class _ConfigEntry:
    def __class_getitem__(cls, item):
        return cls


class _ConfigFlowResult(dict):
    pass


class _ConfigFlow:
    context: dict = {}

    def __init_subclass__(cls, domain: str = "", **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, uid: str) -> None:
        pass

    def _abort_if_unique_id_configured(self, **kwargs) -> None:
        pass

    def _get_reconfigure_entry(self):
        return None

    def async_show_form(self, *, step_id, data_schema=None, errors=None, **kwargs):
        return {"type": "form", "step_id": step_id, "errors": errors or {}}

    def async_create_entry(self, *, title="", data):
        return {"type": "create_entry", "title": title, "data": data}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}

    def async_update_reload_and_abort(self, entry, *, data, **kwargs):
        return {"type": "update_and_abort", "data": data}


class _OptionsFlowWithReload(_ConfigFlow):
    config_entry = None


_ha_config_entries.ConfigEntry = _ConfigEntry
_ha_config_entries.ConfigFlow = _ConfigFlow
_ha_config_entries.ConfigFlowResult = _ConfigFlowResult
_ha_config_entries.OptionsFlowWithReload = _OptionsFlowWithReload

_ha_const = types.ModuleType("homeassistant.const")
_ha_const.CONF_HOST = "host"
_ha_const.CONF_PORT = "port"

class _Platform:
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    LOCK = "lock"
    NUMBER = "number"
    SENSOR = "sensor"
    SWITCH = "switch"
    VALVE = "valve"

_ha_const.Platform = _Platform

_ha_exceptions = types.ModuleType("homeassistant.exceptions")
class _ConfigEntryNotReady(Exception):
    pass
_ha_exceptions.ConfigEntryNotReady = _ConfigEntryNotReady

_ha_helpers = types.ModuleType("homeassistant.helpers")
_ha_service_info = types.ModuleType("homeassistant.helpers.service_info")
_ha_service_info_zeroconf = types.ModuleType("homeassistant.helpers.service_info.zeroconf")
_ha_service_info_zeroconf.ZeroconfServiceInfo = object
_ha_entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
_ha_entity_registry.async_get = lambda hass: None
_ha_entity_registry.async_entries_for_config_entry = lambda reg, entry_id: []
_ha_helpers.entity_registry = _ha_entity_registry

_ha_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
_ha_entity_platform.AddConfigEntryEntitiesCallback = object
_ha_helpers.entity_platform = _ha_entity_platform

_ha_device_registry = types.ModuleType("homeassistant.helpers.device_registry")


class _DeviceInfo(dict):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)


_ha_device_registry.DeviceInfo = _DeviceInfo
_ha_helpers.device_registry = _ha_device_registry

_ha_entity = types.ModuleType("homeassistant.helpers.entity")


class _Entity:
    _attr_has_entity_name = False
    _attr_should_poll = True
    _attr_unique_id = None
    _attr_name = None
    _attr_available = False
    _attr_device_info = None

    def async_on_remove(self, fn):
        pass

    def async_write_ha_state(self):
        pass

    async def async_added_to_hass(self):
        pass


_ha_entity.Entity = _Entity
_ha_helpers.entity = _ha_entity

_ha_components = types.ModuleType("homeassistant.components")
_ha_zeroconf = types.ModuleType("homeassistant.components.zeroconf")
_mock_aiozc = AsyncMock()
_ha_zeroconf.async_get_async_instance = AsyncMock(return_value=_mock_aiozc)

# ---- lock ----


class _LockState(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"
    LOCKING = "locking"
    UNLOCKING = "unlocking"
    JAMMED = "jammed"
    OPEN = "open"
    OPENING = "opening"


class _LockEntity(_Entity):
    _attr_is_locked = None
    _attr_is_locking = False
    _attr_is_unlocking = False
    _attr_is_jammed = False
    _attr_is_open = False
    _attr_is_opening = False
    _attr_assumed_state = False


_ha_lock = types.ModuleType("homeassistant.components.lock")
_ha_lock.LockEntity = _LockEntity
_ha_lock.LockState = _LockState
_ha_components.lock = _ha_lock

# ---- valve ----


class _ValveDeviceClass(str, Enum):
    WATER = "water"
    GAS = "gas"


class _ValveEntityFeature:
    OPEN = 1
    CLOSE = 2
    STOP = 4
    SET_POSITION = 8


class _ValveEntity(_Entity):
    _attr_reports_position = False
    _attr_supported_features = 0
    _attr_current_valve_position = None
    _attr_is_opening = False
    _attr_is_closing = False
    _attr_assumed_state = False
    _attr_device_class = None


_ha_valve = types.ModuleType("homeassistant.components.valve")
_ha_valve.ValveDeviceClass = _ValveDeviceClass
_ha_valve.ValveEntity = _ValveEntity
_ha_valve.ValveEntityFeature = _ValveEntityFeature
_ha_components.valve = _ha_valve

# ---- switch ----

class _SwitchEntity(_Entity):
    _attr_is_on = False
    _attr_assumed_state = False


_ha_switch = types.ModuleType("homeassistant.components.switch")
_ha_switch.SwitchEntity = _SwitchEntity
_ha_components.switch = _ha_switch

# ---- number ----

class _NumberMode(str, Enum):
    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


class _NumberDeviceClass(str, Enum):
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"


class _NumberEntity(_Entity):
    _attr_native_value = None
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_mode = None
    _attr_assumed_state = False
    _attr_device_class = None


_ha_number = types.ModuleType("homeassistant.components.number")
_ha_number.NumberEntity = _NumberEntity
_ha_number.NumberMode = _NumberMode
_ha_number.NumberDeviceClass = _NumberDeviceClass
_ha_components.number = _ha_number

for _name, _mod in [
    ("homeassistant", _ha_stub),
    ("homeassistant.core", _ha_core),
    ("homeassistant.config_entries", _ha_config_entries),
    ("homeassistant.const", _ha_const),
    ("homeassistant.exceptions", _ha_exceptions),
    ("homeassistant.helpers", _ha_helpers),
    ("homeassistant.helpers.service_info", _ha_service_info),
    ("homeassistant.helpers.service_info.zeroconf", _ha_service_info_zeroconf),
    ("homeassistant.helpers.entity_registry", _ha_entity_registry),
    ("homeassistant.helpers.entity_platform", _ha_entity_platform),
    ("homeassistant.helpers.device_registry", _ha_device_registry),
    ("homeassistant.helpers.entity", _ha_entity),
    ("homeassistant.components", _ha_components),
    ("homeassistant.components.zeroconf", _ha_zeroconf),
    ("homeassistant.components.lock", _ha_lock),
    ("homeassistant.components.valve", _ha_valve),
    ("homeassistant.components.switch", _ha_switch),
    ("homeassistant.components.number", _ha_number),
]:
    sys.modules.setdefault(_name, _mod)

# ---------------------------------------------------------------------------
# Now safe to import coordinator
# ---------------------------------------------------------------------------

from coap_client_for_esphome.coordinator import (  # noqa: E402
    CoapCoordinator,
    _SimpleOscoreSecurityContext,
    _parse_cbor_state,
    _parse_link_format,
)
from coap_client_for_esphome.const import (  # noqa: E402
    CONF_ID_CONTEXT,
    CONF_MASTER_SALT,
    CONF_MASTER_SECRET,
    CONF_OSCORE_SEQ_THRESHOLD,
    CONF_RECIPIENT_ID,
    CONF_SENDER_ID,
    SENML_V,
    SENML_VB,
    SENML_VS,
)

# ---------------------------------------------------------------------------
# Mock HomeAssistant
# ---------------------------------------------------------------------------

_SERVER_HOST = "127.0.0.1"


class _ConfigEntries:
    def __init__(self) -> None:
        self.reload_calls: list[str] = []

    def async_schedule_reload(self, entry_id: str) -> None:
        self.reload_calls.append(entry_id)


class MockHass:
    """Minimal HomeAssistant stand-in for coordinator tests."""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self.config_entries = _ConfigEntries()

    @property
    def loop(self):
        return asyncio.get_event_loop()

    def async_create_background_task(
        self, coro, *, name: str = ""
    ) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        if name:
            task.set_name(name)
        self._tasks.append(task)
        return task

    async def cancel_all_tasks(self) -> None:
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()


# ---------------------------------------------------------------------------
# Mock CoAP server resources
# ---------------------------------------------------------------------------


def _free_udp_port() -> int:
    """Return an available UDP port on loopback."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((_SERVER_HOST, 0))
        return s.getsockname()[1]


class _EntityResource(resource.ObservableResource):
    """Observable resource that returns CBOR SenML payloads."""

    def __init__(self, value, value_type: str = "v") -> None:
        super().__init__()
        self._value = value
        self._value_type = value_type  # "v" float, "vb" bool, "vs" str

    @property
    def value(self):
        return self._value

    def set_value(self, value) -> None:
        self._value = value
        self.updated_state()

    def _payload(self) -> bytes:
        if self._value_type == "vb":
            return cbor2.dumps([{SENML_VB: bool(self._value)}])
        if self._value_type == "vs":
            return cbor2.dumps([{SENML_VS: str(self._value)}])
        if self._value_type == "v_uint":
            return cbor2.dumps([{SENML_V: int(self._value)}])
        return cbor2.dumps([{SENML_V: float(self._value)}])

    async def render_get(self, request):
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=self._payload(),
            content_format=60,
        )


class _InfoResource(resource.Resource):
    def __init__(self, info: dict) -> None:
        super().__init__()
        self._info = info

    async def render_get(self, request):
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=cbor2.dumps(self._info),
            content_format=60,
        )


class _PingResource(resource.Resource):
    def __init__(self) -> None:
        super().__init__()
        self._uptime: int | None = None

    def set_uptime(self, uptime: int | None) -> None:
        self._uptime = uptime

    async def render_get(self, request):
        payload = b""
        if self._uptime is not None:
            payload = cbor2.dumps([{SENML_V: float(self._uptime)}])
        return aiocoap.Message(transport_tuning=aiocoap.Unreliable, code=aiocoap.CONTENT, payload=payload)


class _LogResource(resource.ObservableResource):
    """Observable resource that sends log notifications in ESPHome log format.

    Entries are encoded as cbor2.dumps([[millis, level, tag, message], ...]).
    This matches what _forward_logs() expects, unlike the SenML encoding used
    by _EntityResource.
    """

    def __init__(self) -> None:
        super().__init__()
        self._entries: list = []
        self.deregister_received: bool = False

    def notify(self, entries: list) -> None:
        """Trigger an observation notification with the given log entries."""
        self._entries = entries
        self.updated_state()

    async def render_get(self, request):
        if request.opt.observe == 1:
            self.deregister_received = True
            # Remove stale observation entries so subsequent subscribers
            # are not blocked by dead-endpoint delivery failures.
            self._observations.clear()
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=cbor2.dumps(self._entries),
            content_format=60,
        )


class _TerminatingEntityResource(resource.ObservableResource):
    """Entity resource that can terminate all its observations on demand."""

    def __init__(self, value: float = 0.0) -> None:
        super().__init__()
        self._value = value
        self.observe_request_count: int = 0

    def _payload(self) -> bytes:
        return cbor2.dumps([{SENML_V: float(self._value)}])

    async def render_get(self, request):
        if request.opt.observe == 0:
            self.observe_request_count += 1
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=self._payload(),
            content_format=60,
        )

    def end_all_observations(self) -> None:
        """Terminate all active observations (server-initiated deregistration)."""
        for obs in list(self._observations):
            obs.trigger(is_last=True)


class _MtypeCapturingEntityResource(resource.ObservableResource):
    """Entity resource that records the mtype of each incoming observe request."""

    def __init__(self, value: float = 0.0) -> None:
        super().__init__()
        self._value = value
        self.received_mtypes: list = []

    def _payload(self) -> bytes:
        return cbor2.dumps([{SENML_V: float(self._value)}])

    async def render_get(self, request):
        if request.opt.observe == 0:
            self.received_mtypes.append(request.mtype)
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=self._payload(),
            content_format=60,
        )


class _OscoreEnforcingEntityResource(resource.ObservableResource):
    """Observable resource that rejects plaintext requests with 4.01 Unauthorized."""

    def __init__(self, value, value_type: str = "v") -> None:
        super().__init__()
        self._inner = _EntityResource(value, value_type)

    @property
    def value(self):
        return self._inner.value

    def set_value(self, value) -> None:
        self._inner.set_value(value)
        self.updated_state()

    async def render_get(self, request):
        if request.opt.oscore is None:
            return aiocoap.Message(code=aiocoap.UNAUTHORIZED)
        return await self._inner.render_get(request)


class _OscoreServerEntityResource(resource.ObservableResource):
    """Entity resource that performs real server-side OSCORE decryption.

    Plaintext GET → 4.01 Unauthorized.
    OSCORE-protected POST (coordinator remaps FETCH+observe → POST) →
    unprotect with server context, call inner handler, protect response.
    """

    def __init__(self, inner: _EntityResource, server_ctx) -> None:
        super().__init__()
        self._inner = inner
        self._server_ctx = server_ctx

    @property
    def value(self):
        return self._inner.value

    def set_value(self, value) -> None:
        self._inner.set_value(value)
        self.updated_state()

    async def render_get(self, request):
        return aiocoap.Message(code=aiocoap.UNAUTHORIZED)

    async def render_post(self, request):
        if request.opt.oscore is None:
            return aiocoap.Message(code=aiocoap.UNAUTHORIZED)
        try:
            inner_request, req_id = self._server_ctx.unprotect(request)
            inner_response = await self._inner.render_get(inner_request)
            protected_response, _ = self._server_ctx.protect(inner_response, req_id)
            return protected_response
        except Exception:
            return aiocoap.Message(code=aiocoap.UNAUTHORIZED)


class _WKCResource(resource.Resource):
    def __init__(self, server: "MockCoapServer") -> None:
        super().__init__()
        self._server = server

    async def render_get(self, request):
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=self._server.link_format().encode(),
            content_format=40,  # application/link-format
        )


# ---------------------------------------------------------------------------
# MockCoapServer
# ---------------------------------------------------------------------------

# Default link format used by all basic tests
DEFAULT_LINK_FORMAT = (
    '</fp/1>;rt="esphome.sensor";obs;oid="temperature";uom="°C";dc="temperature",'
    '</fp/2>;rt="esphome.binary_sensor";obs;oid="motion",'
    '</fp/3>;rt="esphome.switch";obs;oid="relay",'
    '</fp/9/g/1>;rt="esphome.log";obs;oid="logs",'
    '</ping>;rt="esphome.ping",'
    '</info>;rt="esphome.device"'
)

# Extends the default with lock (fp/7) and valve (fp/8)
LOCK_VALVE_LINK_FORMAT = (
    '</fp/1>;rt="esphome.sensor";obs;oid="temperature";uom="°C";dc="temperature",'
    '</fp/2>;rt="esphome.binary_sensor";obs;oid="motion",'
    '</fp/3>;rt="esphome.switch";obs;oid="relay",'
    '</fp/7>;rt="esphome.lock";obs;oid="door_lock",'
    '</fp/8>;rt="esphome.valve";obs;oid="garden_valve";stp=/fp/8/stop,'
    '</fp/9/g/1>;rt="esphome.log";obs;oid="logs",'
    '</ping>;rt="esphome.ping",'
    '</info>;rt="esphome.device"'
)

# Mirrors the entity set in test-espc6-pm-coap-full.yaml
FULL_LINK_FORMAT = (
    '</fp/1>;rt="esphome.sensor";obs;oid="uptime";uom="s";dc="duration",'
    '</fp/2>;rt="esphome.switch";obs;oid="radio_always_on",'
    '</fp/3>;rt="esphome.button";oid="button",'
    '</fp/4>;rt="esphome.binary_sensor";obs;oid="binary_sensor",'
    '</fp/5>;rt="esphome.text_sensor";obs;oid="text_sensor",'
    '</fp/6>;rt="esphome.number";obs;oid="number",'
    '</fp/7>;rt="esphome.lock";obs;oid="lock",'
    '</fp/8>;rt="esphome.valve";obs;oid="valve",'
    '</fp/9/g/1>;rt="esphome.log";obs;oid="logs",'
    '</ping>;rt="esphome.ping",'
    '</info>;rt="esphome.device"'
)

REDUCED_LINK_FORMAT = (
    '</fp/1>;rt="esphome.sensor";obs;oid="uptime";uom="s";dc="duration",'
    '</fp/9/g/1>;rt="esphome.log";obs;oid="logs",'
    '</ping>;rt="esphome.ping",'
    '</info>;rt="esphome.device"'
)

DEVICE_NAME = "test_device"
FRIENDLY_NAME = "Test Device"


class MockCoapServer:
    """Simulates an ESPHome CoAP server for testing coordinator.py.

    Fixed resource set: temperature (sensor), motion (binary_sensor),
    relay (switch), logs (log), ping, info.
    """

    def __init__(
        self,
        link_format: str = DEFAULT_LINK_FORMAT,
        oscore_required: bool = False,
        oscore_server_ctx=None,
    ) -> None:
        self._site = resource.Site()
        self._port = _free_udp_port()
        self._context: aiocoap.Context | None = None
        self._link_format = link_format

        def _make(value, vtype):
            if oscore_server_ctx is not None:
                return _OscoreServerEntityResource(_EntityResource(value, vtype), oscore_server_ctx)
            if oscore_required:
                return _OscoreEnforcingEntityResource(value, vtype)
            return _EntityResource(value, vtype)

        self._entities: dict[str, _EntityResource] = {
            "temperature": _make(20.0, "v"),
            "motion": _make(False, "vb"),
            "relay": _make(False, "vb"),
            "door_lock": _make(2, "v_uint"),  # 2 = UNLOCKED
            "garden_valve": _make(0.0, "v"),  # 0.0 = closed
        }
        self._log = _LogResource()
        self._ping = _PingResource()

        self._site.add_resource(["fp", "1"], self._entities["temperature"])
        self._site.add_resource(["fp", "2"], self._entities["motion"])
        self._site.add_resource(["fp", "3"], self._entities["relay"])
        self._site.add_resource(["fp", "7"], self._entities["door_lock"])
        self._site.add_resource(["fp", "8"], self._entities["garden_valve"])
        self._site.add_resource(["fp", "9", "g", "1"], self._log)
        self._site.add_resource(["ping"], self._ping)
        self._site.add_resource(
            ["info"],
            _InfoResource(
                {
                    "name": DEVICE_NAME,
                    "friendly_name": FRIENDLY_NAME,
                    "version": "1.0.0",
                    "build_time": "2024-01-01",
                    "model": "ESP32-C6",
                    "ping_interval": 60,
                    "ping_timeout": 10,
                    "ping_retry": 1,
                    "areas": [],
                    "devices": [
                        {
                            "name": DEVICE_NAME,
                            "friendly_name": FRIENDLY_NAME,
                            "model": "ESP32-C6",
                        }
                    ],
                }
            ),
        )
        self._site.add_resource([".well-known", "core"], _WKCResource(self))

    def link_format(self) -> str:
        return self._link_format

    def set_link_format(self, fmt: str) -> None:
        self._link_format = fmt

    @property
    def host(self) -> str:
        return _SERVER_HOST

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        self._context = await aiocoap.Context.create_server_context(
            self._site, bind=(_SERVER_HOST, self._port)
        )

    async def stop(self) -> None:
        if self._context is not None:
            await self._context.shutdown()
            self._context = None

    def set_value(self, name: str, value) -> None:
        self._entities[name].set_value(value)

    def get_value(self, name: str):
        return self._entities[name].value

    def set_uptime(self, uptime: int | None) -> None:
        self._ping.set_uptime(uptime)

    def trigger_log_notification(self, entries: list) -> None:
        """Push a log notification to subscribed observers.

        Each entry is [millis, level, tag, message] matching ESPHome log format.
        """
        self._log.notify(entries)

    def clear_log_observers(self) -> None:
        """Remove all registered log observers (call between coordinator restarts)."""
        self._log._observations.clear()


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def mock_server():
    server = MockCoapServer()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def lock_valve_server():
    server = MockCoapServer(link_format=LOCK_VALVE_LINK_FORMAT)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def oscore_required_server():
    """Mock server that rejects plaintext requests to entity resources with 4.01."""
    server = MockCoapServer(oscore_required=True)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
def hass():
    return MockHass()


@pytest.fixture
async def coordinator(hass, mock_server):
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
    )
    yield coord
    await coord.async_teardown()
    await hass.cancel_all_tasks()


@pytest.fixture
async def lock_valve_coordinator(hass, lock_valve_server):
    coord = CoapCoordinator(
        hass=hass,
        host=lock_valve_server.host,
        port=lock_valve_server.port,
    )
    yield coord
    await coord.async_teardown()
    await hass.cancel_all_tasks()


@pytest.fixture
async def oscore_server_with_decrypt():
    """Mock server that performs real server-side OSCORE decryption/encryption.

    Uses the same key material as the oscore_coordinator fixture (swapped IDs):
      sender_id=0x01 (server's own ID = client's recipient_id)
      recipient_id=0x02 (client's sender_id)
    """
    server_ctx = _SimpleOscoreSecurityContext(
        master_secret=bytes.fromhex("0102030405060708090a0b0c0d0e0f10"),
        master_salt=bytes.fromhex("9e7ca92223786340"),
        sender_id=b"\x01",
        recipient_id=b"\x02",
        id_context=None,
    )
    server = MockCoapServer(oscore_server_ctx=server_ctx)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def oscore_coordinator(hass, mock_server):
    """Coordinator pre-configured with OSCORE credentials."""
    oscore_cfg = {
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "9e7ca92223786340",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        CONF_OSCORE_SEQ_THRESHOLD: "0",
    }
    saved_thresholds: list[int] = []
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        oscore_config=oscore_cfg,
        oscore_save_callback=lambda t: saved_thresholds.append(t),
    )
    coord._saved_thresholds = saved_thresholds
    yield coord
    await coord.async_teardown()
    await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Re-export symbols tests import from conftest
# ---------------------------------------------------------------------------

__all__ = [
    "CoapCoordinator",
    "MockCoapServer",
    "MockHass",
    "_SimpleOscoreSecurityContext",
    "_parse_cbor_state",
    "_parse_link_format",
    "CONF_ID_CONTEXT",
    "CONF_MASTER_SALT",
    "CONF_MASTER_SECRET",
    "CONF_OSCORE_SEQ_THRESHOLD",
    "CONF_RECIPIENT_ID",
    "CONF_SENDER_ID",
    "LOCK_VALVE_LINK_FORMAT",
]
