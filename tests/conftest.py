"""Shared fixtures and mock infrastructure for coordinator tests.

HA module stubs are installed at module level so they exist in sys.modules
before coordinator.py is first imported.
"""

import asyncio
import socket
import sys
import types
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


_ha_config_entries.ConfigEntry = _ConfigEntry

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
_ha_entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")
_ha_entity_registry.async_get = lambda hass: None
_ha_entity_registry.async_entries_for_config_entry = lambda reg, entry_id: []
_ha_helpers.entity_registry = _ha_entity_registry

_ha_components = types.ModuleType("homeassistant.components")
_ha_zeroconf = types.ModuleType("homeassistant.components.zeroconf")
_mock_aiozc = AsyncMock()
_ha_zeroconf.async_get_async_instance = AsyncMock(return_value=_mock_aiozc)

for _name, _mod in [
    ("homeassistant", _ha_stub),
    ("homeassistant.core", _ha_core),
    ("homeassistant.config_entries", _ha_config_entries),
    ("homeassistant.const", _ha_const),
    ("homeassistant.exceptions", _ha_exceptions),
    ("homeassistant.helpers", _ha_helpers),
    ("homeassistant.helpers.entity_registry", _ha_entity_registry),
    ("homeassistant.components", _ha_components),
    ("homeassistant.components.zeroconf", _ha_zeroconf),
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
        return aiocoap.Message(mtype=aiocoap.NON, code=aiocoap.CONTENT, payload=payload)


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
        }
        self._log = _LogResource()
        self._ping = _PingResource()

        self._site.add_resource(["fp", "1"], self._entities["temperature"])
        self._site.add_resource(["fp", "2"], self._entities["motion"])
        self._site.add_resource(["fp", "3"], self._entities["relay"])
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
]
