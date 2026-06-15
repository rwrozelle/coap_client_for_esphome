"""CoAP coordinator for the CoAP Client integration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
import logging
import random
import re
import secrets as _secrets
import sys
import types
from typing import Any

import aiocoap


def _ensure_edhoc_stubs() -> None:
    """Inject stub modules for EDHOC deps not needed for pre-shared-key OSCORE.

    aiocoap's oscore transport imports edhoc.py which imports lakers at module
    level, and oscore_missing_modules() also checks for ge25519. Neither package
    is needed for basic OSCORE with pre-shared keys (they exist for EDHOC key
    establishment). Stub them so the OSCORE transport loads without requiring
    hard-to-build Rust/native packages.
    """
    for mod_name in ("lakers", "ge25519"):
        if mod_name not in sys.modules:
            try:
                __import__(mod_name)
            except ImportError:
                sys.modules[mod_name] = types.ModuleType(mod_name)


_ensure_edhoc_stubs()
from aiocoap.oscore import (
    DEFAULT_ALGORITHM,
    DEFAULT_HASHFUNCTION,
    DEFAULT_WINDOWSIZE,
    CanProtect,
    CanUnprotect,
    ReplayWindow,
    SecurityContextUtils,
    algorithms,
    hashfunctions,
)
import aiocoap.resource as aiocoap_resource
import cbor2
from zeroconf import ServiceListener
from zeroconf.asyncio import AsyncZeroconf

from homeassistant.components import zeroconf
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_ID_CONTEXT,
    CONF_MASTER_SALT,
    CONF_MASTER_SECRET,
    CONF_OSCORE_SEQ_THRESHOLD,
    CONF_RECIPIENT_ID,
    CONF_SENDER_ID,
    DEFAULT_PING_TIMEOUT_S,
    DEFAULT_PORT,
    RT_ACTION,
    RT_BINARY_SENSOR,
    RT_BUTTON,
    RT_DEVICE,
    RT_LOG,
    RT_LOCK,
    RT_NUMBER,
    RT_PING,
    RT_SENSOR,
    RT_SWITCH,
    RT_TEXT_SENSOR,
    RT_VALVE,
    SENML_U,
    SENML_V,
    SENML_VB,
    SENML_VS,
)

_OSCORE_SEQ_INTERVAL = 1024

_ESPHOME_TO_PY_LEVEL: dict[int, int] = {
    1: logging.ERROR,
    2: logging.WARNING,
    3: logging.INFO,
    4: logging.DEBUG,
    5: logging.DEBUG,
}


class _SimpleOscoreSecurityContext(CanProtect, CanUnprotect, SecurityContextUtils):
    """In-memory OSCORE security context with threshold-based sequence number persistence."""

    def __init__(
        self,
        master_secret: bytes,
        master_salt: bytes,
        sender_id: bytes,
        recipient_id: bytes,
        id_context: bytes | None,
        initial_seq_no: int = 0,
        on_threshold: Callable[[int], None] | None = None,
    ) -> None:
        self.alg_aead = algorithms[DEFAULT_ALGORITHM]
        self.hashfun = hashfunctions[DEFAULT_HASHFUNCTION]
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        self.id_context = id_context
        self.echo_recovery = _secrets.token_bytes(8)
        self.sender_sequence_number = initial_seq_no
        self._oscore_seq_threshold = initial_seq_no + _OSCORE_SEQ_INTERVAL
        self._on_threshold = on_threshold
        self.recipient_replay_window = ReplayWindow(DEFAULT_WINDOWSIZE, lambda: None)
        self.recipient_replay_window.initialize_empty()
        self.authenticated_claims: list[str] = []
        self.derive_keys(master_salt, master_secret)

    def protect(self, message, request_id=None, **kwargs):
        protected_msg, req_id = super().protect(message, request_id=request_id, **kwargs)
        # OpenThread's IsRequest() only covers GET/POST/PUT/DELETE (0x01-0x04);
        # FETCH (0x05) is treated as a response and RST'd. Remap to POST.
        if protected_msg.code == aiocoap.FETCH:
            protected_msg = protected_msg.copy(code=aiocoap.POST)
        # RFC 8613 treats Uri-Path as Class E (encrypted, inner-only), so the outer
        # OSCORE message has no Uri-Path. OpenThread routes by Uri-Path before decryption
        # and sends 4.04 Not Found for anything it can't match. Copy the original
        # Uri-Path into the outer message so the server can route to the right handler.
        if message.opt.uri_path:
            protected_msg.opt.uri_path = message.opt.uri_path
        # Preserve the original message type (CON/NON) on the outer message so the
        # server can infer the desired notification type per RFC 7641 §3.5.
        if message.mtype is not None:
            # Input was created with deprecated mtype= — map to transport_tuning
            tuning = aiocoap.Reliable if message.mtype == aiocoap.CON else aiocoap.Unreliable
            protected_msg = protected_msg.copy(transport_tuning=tuning)
        elif message.transport_tuning in (aiocoap.Reliable, aiocoap.Unreliable):
            # Input was created with new transport_tuning= API — copy directly
            protected_msg = protected_msg.copy(transport_tuning=message.transport_tuning)
        return protected_msg, req_id

    @property
    def oscore_seq_threshold(self) -> int:
        """Current sequence number threshold for persistence."""
        return self._oscore_seq_threshold

    def post_seqnoincrease(self) -> None:
        if self.sender_sequence_number >= self._oscore_seq_threshold:
            self._oscore_seq_threshold = (
                self.sender_sequence_number + _OSCORE_SEQ_INTERVAL
            )
            if self._on_threshold is not None:
                self._on_threshold(self._oscore_seq_threshold)


_LOGGER = logging.getLogger(__name__)


class _AiocoapPipeEndedFilter(logging.Filter):
    """Suppress aiocoap's benign 'Response added after pipe ended' warning.

    Fires when a NON observe notification arrives after the client has cancelled
    the observation — a normal race condition, not a functional error.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "has already ended" not in record.getMessage()


def _install_aiocoap_pipe_filter() -> None:
    logger = logging.getLogger("coap-server")
    if not any(isinstance(f, _AiocoapPipeEndedFilter) for f in logger.filters):
        logger.addFilter(_AiocoapPipeEndedFilter())


_BACKOFF_BASE_S = 10.0
_BACKOFF_MAX_S = 300.0


@dataclass
class CoapResource:
    """A discovered CoAP resource from .well-known/core."""

    path: str
    resource_type: str = ""
    interface: str = ""
    content_type: int = 60
    observable: bool = False
    title: str = ""
    name: str = ""
    unit: str = ""
    device_class: str = ""
    accuracy_decimals: int | None = None
    device_index: int = 0
    stop_path: str = ""
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None


@dataclass
class CoapDeviceInfo:
    """Device information from the /info endpoint."""

    name: str = ""
    friendly_name: str = ""
    version: str = ""
    build_time: str = ""
    model: str = ""
    ping_interval_s: int = 60
    ping_timeout_s: int = DEFAULT_PING_TIMEOUT_S
    ping_retry: int = 1
    subscription_confirm: bool = False
    observe_retry: int = 0
    areas: list[dict[str, str]] = field(default_factory=list)
    devices: list[dict[str, Any]] = field(default_factory=list)


class _PingResource(aiocoap_resource.Resource):
    """Minimal CoAP resource that responds NON 2.05 to GET /ping."""

    def __init__(self, coordinator: CoapCoordinator) -> None:
        super().__init__()
        self._coordinator = coordinator

    async def render_get(self, request: aiocoap.Message) -> aiocoap.Message:
        _LOGGER.debug(
            "Device-initiated ping received from %s",
            request.remote,
        )
        self._coordinator.record_server_pong()
        return aiocoap.Message(
            transport_tuning=aiocoap.Unreliable,
            code=aiocoap.CONTENT,
        )


class CoapCoordinator:
    """Manages a CoAP connection, observations, and entity state."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int = DEFAULT_PORT,
        oscore_config: dict[str, str] | None = None,
        oscore_save_callback: Callable[[int], None] | None = None,
        entry_id: str = "",
        subscribe_logs: bool = False,
        observe_retry_initial_delay_s: float = _BACKOFF_BASE_S,
        backoff_base_s: float = _BACKOFF_BASE_S,
        resubscribe_interval_s: float = 86400.0,
    ) -> None:
        """Initialize the CoAP coordinator."""
        self.hass = hass
        self.host = host
        self.port = port
        self._entry_id = entry_id
        self._subscribe_logs = subscribe_logs
        self._observe_retry_initial_delay_s = observe_retry_initial_delay_s
        self._backoff_base_s = backoff_base_s
        self._resubscribe_interval_s = resubscribe_interval_s
        self.device_info = CoapDeviceInfo()
        self.resources: list[CoapResource] = []
        self._context: aiocoap.Context | None = None
        self._oscore_config = oscore_config
        self._oscore_save_callback = oscore_save_callback
        self._oscore_ctx: _SimpleOscoreSecurityContext | None = None
        self._state: dict[str, dict[str, Any]] = {}
        self._subscriptions: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._availability_callbacks: list[Callable[[bool], None]] = []
        self._observe_tasks: list[asyncio.Task] = []
        self._ping_task: asyncio.Task | None = None
        self._ping_wakeup: asyncio.Event = asyncio.Event()
        self._backoff_task: asyncio.Task | None = None
        self._available = False
        self._last_server_pong: float = 0.0
        self._last_device_uptime: int | None = None
        self._had_ping_since_reconnect: bool = False
        self._consecutive_ping_misses: int = 0
        self._reconnect_task: asyncio.Task | None = None
        self._zeroconf_unsub: Callable[[], Awaitable[None]] | None = None
        self._zeroconf_task: asyncio.Task | None = None

    def _uri(self, path: str) -> str:
        host = (
            f"[{self.host}]"
            if ":" in self.host and not self.host.startswith("[")
            else self.host
        )
        return f"coap://{host}:{self.port}/{path}"

    async def async_setup(self) -> None:
        """Fetch /info and .well-known/core to bootstrap the integration."""
        _install_aiocoap_pipe_filter()
        site = aiocoap_resource.Site()
        site.add_resource(["ping"], _PingResource(self))
        self._context = await aiocoap.Context.create_server_context(
            site, bind=("::", 0)
        )
        await self._async_fetch_info()
        await self._async_fetch_resources()
        if self._oscore_config:
            self._configure_oscore()

    def _configure_oscore(self) -> None:
        """Build the OSCORE security context and register credentials for entity resource paths."""
        from aiocoap.defaults import oscore_missing_modules

        missing = oscore_missing_modules()
        if missing:
            _LOGGER.error(
                "OSCORE cannot be enabled: missing modules %s — requests will be unencrypted",
                missing,
            )
            return

        cfg = self._oscore_config
        assert cfg is not None
        master_secret = bytes.fromhex(cfg[CONF_MASTER_SECRET])
        master_salt = (
            bytes.fromhex(cfg[CONF_MASTER_SALT]) if cfg[CONF_MASTER_SALT] else b""
        )
        sender_id = bytes.fromhex(cfg[CONF_SENDER_ID])
        recipient_id = bytes.fromhex(cfg[CONF_RECIPIENT_ID])
        id_context_hex = cfg[CONF_ID_CONTEXT]
        id_context = bytes.fromhex(id_context_hex) if id_context_hex else None
        try:
            initial_seq_no = int(cfg.get(CONF_OSCORE_SEQ_THRESHOLD, 0))
        except (TypeError, ValueError):
            _LOGGER.warning("OSCORE seq threshold invalid in config, resetting to 0")
            initial_seq_no = 0

        self._oscore_ctx = _SimpleOscoreSecurityContext(
            master_secret=master_secret,
            master_salt=master_salt,
            sender_id=sender_id,
            recipient_id=recipient_id,
            id_context=id_context,
            initial_seq_no=initial_seq_no,
            on_threshold=self._oscore_save_callback,
        )
        # Save next threshold immediately so a crash before the first crossing is safe.
        if self._oscore_save_callback is not None:
            self._oscore_save_callback(self._oscore_ctx.oscore_seq_threshold)
        self._apply_oscore_credentials()
        protected = sum(
            1 for r in self.resources if r.resource_type not in (RT_PING, RT_DEVICE)
        )
        _LOGGER.info("OSCORE enabled for %s (%d protected paths)", self.host, protected)

    def _apply_oscore_credentials(self) -> None:
        """Register OSCORE credentials for each protected resource path.

        Excludes the ping resource so that /ping, /info, and .well-known/core
        remain unencrypted — the server's handlers for those paths do not perform
        OSCORE decryption.
        """
        if self._oscore_ctx is None or self._context is None:
            return
        host_str = (
            f"[{self.host}]"
            if ":" in self.host and not self.host.startswith("[")
            else self.host
        )
        for resource in self.resources:
            if resource.resource_type not in (RT_PING, RT_DEVICE):
                uri = f"coap://{host_str}:{self.port}/{resource.path}"
                self._context.client_credentials[uri] = self._oscore_ctx
                _LOGGER.debug("OSCORE credential registered for %s", uri)
        self._oscore_ctx.authenticated_claims = [f"coap://{host_str}:{self.port}/*"]

    async def _async_fetch_info(self) -> None:
        assert self._context is not None
        _LOGGER.debug("Fetching /info from %s", self.host)
        response = await asyncio.wait_for(
            self._context.request(
                aiocoap.Message(transport_tuning=aiocoap.Unreliable, code=aiocoap.GET, uri=self._uri("info"))
            ).response,
            timeout=self.device_info.ping_timeout_s,
        )
        _LOGGER.debug("Got /info response from %s: code=%s", self.host, response.code)
        if not response.code.is_successful():
            raise OSError(f"/info returned {response.code} from {self.host}")
        try:
            raw = cbor2.loads(response.payload)
            if isinstance(raw, dict):
                self.device_info = CoapDeviceInfo(
                    name=raw.get("name", ""),
                    friendly_name=str(
                        raw.get("friendly_name") or raw.get("name") or ""
                    ),
                    version=raw.get("version", ""),
                    build_time=raw.get("build_time", ""),
                    model=raw.get("model", ""),
                    ping_interval_s=int(raw.get("ping_interval", 60)),
                    ping_timeout_s=int(raw.get("ping_timeout", 150)),
                    ping_retry=int(raw.get("ping_retry", 1)),
                    subscription_confirm=bool(raw.get("subscription_confirm", False)),
                    observe_retry=int(raw.get("observe_retry", 0)),
                    areas=raw.get("areas", []),
                    devices=raw.get("devices", []),
                )
                _LOGGER.debug(
                    "Device info from %s: name=%s version=%s ping_interval=%ds ping_timeout=%ds",
                    self.host,
                    self.device_info.name,
                    self.device_info.version,
                    self.device_info.ping_interval_s,
                    self.device_info.ping_timeout_s,
                )
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Failed to parse /info from %s", self.host)

    async def _async_fetch_resources(self) -> None:
        assert self._context is not None
        _LOGGER.debug("Fetching .well-known/core from %s", self.host)
        response = await asyncio.wait_for(
            self._context.request(
                aiocoap.Message(
                    transport_tuning=aiocoap.Reliable, code=aiocoap.GET, uri=self._uri(".well-known/core"),
                    block2=aiocoap.optiontypes.BlockOption.BlockwiseTuple(0, False, 6),
                )
            ).response,
            timeout=self.device_info.ping_timeout_s,
        )
        _LOGGER.debug(
            "Got .well-known/core response from %s: code=%s", self.host, response.code
        )
        if not response.code.is_successful():
            raise OSError(f".well-known/core returned {response.code} from {self.host}")
        self.resources = _parse_link_format(response.payload.decode("utf-8"))
        _LOGGER.debug(
            "Parsed %d resources from %s: %s",
            len(self.resources),
            self.host,
            [r.path for r in self.resources],
        )

    @callback
    def _start_observe_tasks(self) -> None:
        """Create and register observe tasks for all observable resources."""
        observable = [r for r in self.resources if r.observable]
        _LOGGER.debug(
            "Starting %d observations on %s: %s",
            len(observable),
            self.host,
            [r.path for r in observable],
        )
        for resource in observable:
            if resource.resource_type == RT_LOG:
                if not self._subscribe_logs:
                    continue
                coro = self._async_observe_logs(resource)
                name = f"coap_observe_logs_{self.host}"
            else:
                coro = self._async_observe(resource)
                name = f"coap_observe_{self.host}_{resource.path}"
            task = self.hass.async_create_background_task(coro, name=name)
            self._observe_tasks.append(task)

    def async_start_observations(self) -> None:
        """Start observe loops for all observable resources and the ping task."""
        self._start_observe_tasks()
        self._last_server_pong = self.hass.loop.time()
        if not self.device_info.subscription_confirm:
            self._ping_task = self.hass.async_create_background_task(
                self._async_ping_loop(),
                name=f"coap_ping_{self.host}",
            )
            _LOGGER.debug(
                "Ping loop started for %s (interval=%ds timeout=%ds)",
                self.host,
                self.device_info.ping_interval_s,
                self.device_info.ping_timeout_s,
            )
        else:
            _LOGGER.debug(
                "Skipping ping loop for %s (subscription_confirm=True, CON observe handles liveness)",
                self.host,
            )
        self._subscribe_zeroconf()

    def async_resubscribe(self) -> None:
        """Cancel all observe tasks and restart them immediately."""
        _LOGGER.debug("Resubscribing all observations for %s", self.host)
        for task in self._observe_tasks:
            task.cancel()
        self._observe_tasks.clear()
        self._start_observe_tasks()

    async def _async_observe(self, resource: CoapResource) -> None:
        """Observe a resource with periodic resubscription and retry on failure.

        Planned resubscriptions (interval elapsed) restart the retry budget immediately.
        Unplanned exits consume the retry budget with exponential backoff; exhausting
        it marks the coordinator unavailable.
        """
        max_retries = self.device_info.observe_retry
        while True:
            delay = self._observe_retry_initial_delay_s
            for attempt in range(max_retries + 1):
                planned = await self._async_observe_once(resource)
                if self._context is None:
                    return
                if planned:
                    break  # planned resubscription — restart with fresh retry budget
                if attempt < max_retries:
                    _LOGGER.debug(
                        "Retrying observe for %s on %s in %.0fs (attempt %d/%d)",
                        resource.path,
                        self.host,
                        delay,
                        attempt + 1,
                        max_retries,
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, _BACKOFF_MAX_S)
                else:
                    _LOGGER.warning(
                        "Observation for %s on %s exhausted all retries, marking unavailable",
                        resource.path,
                        self.host,
                    )
                    self._set_available(False)
                    if self.device_info.subscription_confirm:
                        self._cancel_observations()
                        self._start_backoff()
                    return

    async def _async_observe_once(self, resource: CoapResource) -> bool:
        """Single observe attempt; returns True if planned resubscription, False on unexpected exit."""
        assert self._context is not None
        transport_tuning = aiocoap.Reliable if self.device_info.subscription_confirm else aiocoap.Unreliable
        _LOGGER.debug("Sending GET+Observe for %s on %s", resource.path, self.host)
        uri = self._uri(resource.path)
        try:
            pr = self._context.request(
                aiocoap.Message(
                    transport_tuning=transport_tuning,
                    code=aiocoap.GET,
                    uri=uri,
                    observe=0,
                )
            )
            response = await pr.response
            _LOGGER.debug(
                "Initial observe response for %s on %s: code=%s has_observation=%s",
                resource.path,
                self.host,
                response.code,
                pr.observation is not None,
            )
            if not response.code.is_successful():
                _LOGGER.warning(
                    "Observe setup failed for %s on %s: %s",
                    resource.path,
                    self.host,
                    response.code,
                )
                self._set_available(False)
                return False
            self._deliver(resource.name, response.payload)
            self._set_available(True)
            if pr.observation is not None:
                planned = False
                try:
                    jitter_s = random.uniform(
                        self._resubscribe_interval_s * 0.75,
                        self._resubscribe_interval_s * 1.25,
                    )
                    async with asyncio.timeout(jitter_s):
                        async for obs in pr.observation:
                            self._deliver(resource.name, obs.payload)
                    _LOGGER.debug(
                        "Observation stream ended for %s on %s", resource.path, self.host
                    )
                except asyncio.TimeoutError:
                    _LOGGER.debug(
                        "Resubscribing %s on %s (interval elapsed)", resource.path, self.host
                    )
                    planned = True
                finally:
                    if self._context is not None:
                        try:
                            self._context.request(
                                aiocoap.Message(
                                    transport_tuning=transport_tuning,
                                    code=aiocoap.GET,
                                    uri=uri,
                                    observe=1,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        pr.observation.cancel()
                    except Exception:  # noqa: BLE001
                        pass  # already cancelled (e.g. server-terminated with is_last)
                return planned
            else:
                _LOGGER.debug(
                    "No observation object for %s on %s — not observable",
                    resource.path,
                    self.host,
                )
        except asyncio.CancelledError:
            _LOGGER.debug(
                "Observe task cancelled for %s on %s", resource.path, self.host
            )
            raise
        except (aiocoap.error.Error, OSError) as err:
            _LOGGER.warning(
                "Observe failed for %s on %s: %s", resource.path, self.host, err
            )
            self._set_available(False)
        return False

    async def _async_observe_logs(self, resource: CoapResource) -> None:
        """Observe the log resource, resubscribing periodically."""
        assert self._context is not None
        device_name = self.device_info.friendly_name or self.device_info.name or self.host
        transport_tuning = aiocoap.Reliable if self.device_info.subscription_confirm else aiocoap.Unreliable
        while True:
            _LOGGER.debug("Starting log observation for %s", self.host)
            try:
                pr = self._context.request(
                    aiocoap.Message(
                        transport_tuning=transport_tuning,
                        code=aiocoap.GET,
                        uri=self._uri(resource.path),
                        observe=0,
                    )
                )
                await pr.response  # initial response is always [] — nothing to forward
                if pr.observation is None:
                    _LOGGER.debug("No observation object for logs on %s", self.host)
                    return
                try:
                    jitter_s = random.uniform(
                        self._resubscribe_interval_s * 0.75,
                        self._resubscribe_interval_s * 1.25,
                    )
                    async with asyncio.timeout(jitter_s):
                        async for obs in pr.observation:
                            self.record_server_pong()
                            self._forward_logs(device_name, obs.payload)
                    _LOGGER.debug("Log observation stream ended for %s", self.host)
                except asyncio.TimeoutError:
                    _LOGGER.debug("Resubscribing log observe on %s (interval elapsed)", self.host)
                finally:
                    if self._context is not None:
                        try:
                            self._context.request(
                                aiocoap.Message(
                                    transport_tuning=transport_tuning,
                                    code=aiocoap.GET,
                                    uri=self._uri(resource.path),
                                    observe=1,
                                )
                            )
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        pr.observation.cancel()
                    except Exception:  # noqa: BLE001
                        pass
                # loop back to resubscribe
            except asyncio.CancelledError:
                _LOGGER.debug("Log observe task cancelled for %s", self.host)
                raise
            except (aiocoap.error.Error, OSError) as err:
                _LOGGER.warning("Log observe failed for %s: %s", self.host, err)
                return  # don't retry on error for logs

    def _forward_logs(self, device_name: str, payload: bytes) -> None:
        """Decode a CBOR log notification and emit each entry to the HA logger."""
        try:
            entries = cbor2.loads(payload)
        except Exception:  # noqa: BLE001
            return
        if not isinstance(entries, list):
            return
        for entry in entries:
            if not (isinstance(entry, list) and len(entry) == 4):
                continue
            _millis, level, tag, message = entry
            py_level = _ESPHOME_TO_PY_LEVEL.get(int(level), logging.DEBUG)
            _LOGGER.log(py_level, "%s: [%s] %s", device_name, tag, message)

    async def _async_ping_loop(self) -> None:
        """Periodically ping the server; trigger backoff/reconnect on silence."""
        interval_s = self.device_info.ping_interval_s
        timeout_s = self.device_info.ping_timeout_s
        _LOGGER.debug(
            "Ping loop entered for %s interval=%ds timeout=%ds",
            self.host,
            interval_s,
            timeout_s,
        )
        while True:
            try:
                await asyncio.wait_for(self._ping_wakeup.wait(), timeout=interval_s)
                self._ping_wakeup.clear()
            except asyncio.TimeoutError:
                pass
            uptime = await self._async_send_ping()
            elapsed = self.hass.loop.time() - self._last_server_pong
            if uptime is None:
                self._consecutive_ping_misses += 1
            else:
                self._consecutive_ping_misses = 0
            _LOGGER.debug(
                "Ping cycle %s: uptime=%s had_ping=%s last_device_uptime=%s elapsed_since_pong=%.0fs consecutive_misses=%d",
                self.host,
                uptime,
                self._had_ping_since_reconnect,
                self._last_device_uptime,
                elapsed,
                self._consecutive_ping_misses,
            )
            if uptime is not None:
                should_reconnect = (
                    uptime == -1 and self._had_ping_since_reconnect
                ) or (
                    self._last_device_uptime is not None
                    and 0 <= uptime < self._last_device_uptime
                )
                if should_reconnect:
                    _LOGGER.info(
                        "CoAP device %s rebooted (uptime signal %d), reconnecting",
                        self.host,
                        uptime,
                    )
                    self._set_available(False)
                    self._cancel_observations()
                    self._last_device_uptime = None
                    self._reconnect_task = self.hass.async_create_background_task(
                        self._async_reconnect_or_backoff(), name=f"coap_reconnect_{self.host}"
                    )
                    return
                if uptime >= 0:
                    self._last_device_uptime = uptime
                self._had_ping_since_reconnect = True
            if self._consecutive_ping_misses >= self.device_info.ping_retry:
                _LOGGER.warning(
                    "CoAP server %s unresponsive after %d consecutive missed pings, entering backoff",
                    self.host,
                    self._consecutive_ping_misses,
                )
                self._set_available(False)
                self._cancel_observations()
                self._start_backoff()
                return

    async def _async_send_ping(self) -> int | None:
        """Send a NON GET to /ping; return device uptime in seconds if present in response."""
        assert self._context is not None
        _LOGGER.debug(
            "Sending ping to %s (timeout=%ds)",
            self.host,
            self.device_info.ping_timeout_s,
        )
        try:
            response = await asyncio.wait_for(
                self._context.request(
                    aiocoap.Message(
                        transport_tuning=aiocoap.Unreliable, code=aiocoap.GET, uri=self._uri("ping")
                    )
                ).response,
                timeout=self.device_info.ping_timeout_s,
            )
            self.record_server_pong()
            _LOGGER.debug(
                "Ping response from %s: code=%s payload_len=%d",
                self.host,
                response.code,
                len(response.payload),
            )
            data = _parse_cbor_state(response.payload)
            if data is not None:
                return int(data["value"])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ping to %s failed: %s", self.host, err)
        return None

    def record_server_pong(self) -> None:
        """Record the timestamp of the most recent server response."""
        self._last_server_pong = self.hass.loop.time()

    def _cancel_observations(self) -> None:
        _LOGGER.debug(
            "Cancelling %d observe tasks and ping task for %s",
            len(self._observe_tasks),
            self.host,
        )
        for task in self._observe_tasks:
            task.cancel()
        self._observe_tasks.clear()
        if self._ping_task is not None:
            self._ping_task.cancel()
            self._ping_task = None

    def _start_backoff(self) -> None:
        if self._backoff_task is not None:
            _LOGGER.debug("Backoff already running for %s, skipping", self.host)
            return
        _LOGGER.debug("Starting backoff reconnect for %s", self.host)
        self._backoff_task = self.hass.async_create_background_task(
            self._async_backoff_reconnect(),
            name=f"coap_backoff_{self.host}",
        )
        self._subscribe_zeroconf()

    def _subscribe_zeroconf(self) -> None:
        """Watch for the device re-announcing on mDNS; triggers reconnect on device reboot."""
        if self._zeroconf_unsub is not None or self._zeroconf_task is not None:
            return
        self._zeroconf_task = self.hass.async_create_background_task(
            self._async_subscribe_zeroconf(),
            name=f"coap_zc_{self.host}",
        )

    async def _async_subscribe_zeroconf(self) -> None:
        """Register a zeroconf service listener for this device."""
        aiozc: AsyncZeroconf = await zeroconf.async_get_async_instance(self.hass)
        device_key = self.device_info.name.lower().replace("-", "_")
        coordinator = self

        class _Listener(ServiceListener):
            def add_service(self, zc, type_: str, name: str) -> None:
                instance = name.split(".", maxsplit=1)[0].lower().replace("-", "_")
                if instance == device_key:
                    coordinator.hass.loop.call_soon_threadsafe(
                        coordinator._trigger_mdns_reconnect
                    )

            def remove_service(self, zc, type_: str, name: str) -> None:
                pass

            def update_service(self, zc, type_: str, name: str) -> None:
                self.add_service(zc, type_, name)

        listener = _Listener()
        await aiozc.async_add_service_listener("_esphome-coap-server._udp.local.", listener)

        async def _remove() -> None:
            await aiozc.async_remove_service_listener(listener)

        self._zeroconf_task = None
        self._zeroconf_unsub = _remove

    @callback
    def _trigger_mdns_reconnect(self) -> None:
        """Handle mDNS re-announcement.

        When the device is currently available, wake the ping loop early so it
        can check the uptime.  If the device has rebooted the uptime will have
        dropped and the ping loop's existing reboot detection will handle the
        reconnect.  Periodic mDNS TTL re-announcements (uptime still counting
        up) cause no further action.

        When the device is not yet available (still reconnecting), ignore the
        announcement — the in-progress reconnect already handles it.
        """
        _LOGGER.debug(
            "mDNS re-announcement from %s (available=%s)", self.host, self._available
        )
        if not self._available:
            _LOGGER.debug(
                "mDNS re-announcement from %s ignored, not yet available", self.host
            )
            return
        if self.device_info.subscription_confirm:
            _LOGGER.debug(
                "mDNS re-announcement from %s ignored (subscription_confirm=True, CON observe handles reboots)",
                self.host,
            )
            return
        _LOGGER.debug("mDNS re-announcement from %s, waking ping loop early", self.host)
        self._ping_wakeup.set()

    async def _async_unsubscribe_zeroconf(self) -> None:
        if self._zeroconf_task is not None:
            self._zeroconf_task.cancel()
            self._zeroconf_task = None
        if self._zeroconf_unsub is not None:
            try:
                await self._zeroconf_unsub()
            except Exception:  # noqa: BLE001
                pass
            self._zeroconf_unsub = None

    async def _async_backoff_reconnect(self) -> None:
        """Exponential backoff reconnect loop."""
        delay = self._backoff_base_s
        _LOGGER.debug("Backoff reconnect loop entered for %s", self.host)
        while True:
            _LOGGER.debug(
                "Backoff sleeping %.0fs before next attempt for %s", delay, self.host
            )
            await asyncio.sleep(delay)
            _LOGGER.debug(
                "Backoff reconnect attempt for %s (delay=%.0fs)", self.host, delay
            )
            try:
                await self._async_send_ping()
                elapsed = self.hass.loop.time() - self._last_server_pong
                _LOGGER.debug(
                    "Backoff ping result for %s: elapsed=%.0fs timeout=%ds",
                    self.host,
                    elapsed,
                    self.device_info.ping_timeout_s,
                )
                if elapsed <= self.device_info.ping_timeout_s:
                    _LOGGER.debug(
                        "Backoff: device %s reachable, triggering reconnect", self.host
                    )
                    try:
                        await self._async_reconnect()
                        return
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.debug(
                            "Reconnect within backoff failed for %s: %s, continuing backoff",
                            self.host,
                            err,
                        )
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("Backoff ping exception for %s: %s", self.host, err)
            delay = min(delay * 2, _BACKOFF_MAX_S)

    def _entity_resource_names(self) -> set[str]:
        """Return the set of resource names that back HA entities (excludes actions and device)."""
        return {
            r.name
            for r in self.resources
            if r.resource_type not in (RT_ACTION, RT_DEVICE, RT_LOG)
        }

    async def _async_reconnect(self) -> None:
        """Re-fetch /info and resources then restart observations. Raises on failure."""
        _LOGGER.debug("Reconnect started for %s", self.host)
        self._backoff_task = None
        self._reconnect_task = None
        self._last_device_uptime = None
        self._had_ping_since_reconnect = False
        self._consecutive_ping_misses = 0
        self._ping_wakeup.clear()
        old_names = self._entity_resource_names()
        _LOGGER.debug("Reconnect: fetching /info for %s", self.host)
        await self._async_fetch_info()
        _LOGGER.debug("Reconnect: fetching resources for %s", self.host)
        await self._async_fetch_resources()
        if self._oscore_ctx is not None:
            self._apply_oscore_credentials()
        new_names = self._entity_resource_names()
        if new_names != old_names:
            _LOGGER.info(
                "Resource set changed on %s (old=%s new=%s), reloading integration",
                self.host,
                old_names,
                new_names,
            )
            self.hass.config_entries.async_schedule_reload(self._entry_id)
            return
        _LOGGER.debug("Reconnect: starting observations for %s", self.host)
        self.async_start_observations()
        _LOGGER.info("Reconnected to CoAP server %s", self.host)

    async def _async_reconnect_or_backoff(self) -> None:
        """Attempt reconnect; fall back to exponential backoff on failure."""
        try:
            await self._async_reconnect()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Reconnect to %s failed: %s, retrying backoff", self.host, err
            )
            self._start_backoff()

    def _deliver(self, name: str, payload: bytes) -> None:
        data = _parse_cbor_state(payload)
        if data is None:
            _LOGGER.debug(
                "Deliver: unparsable payload for %s on %s (len=%d)",
                name,
                self.host,
                len(payload),
            )
            return
        _LOGGER.debug("Deliver: %s on %s = %s", name, self.host, data)
        self._state[name] = data
        self.record_server_pong()
        for cb in self._subscriptions.get(name, []):
            try:
                cb(data)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Deliver callback error for %s on %s: %s", name, self.host, err
                )

    def _set_available(self, available: bool) -> None:
        if available == self._available:
            return
        _LOGGER.debug(
            "Availability changed for %s: %s -> %s",
            self.host,
            self._available,
            available,
        )
        self._available = available
        for cb in self._availability_callbacks:
            cb(available)

    @property
    def available(self) -> bool:
        """Return whether the device is available."""
        return self._available

    def get_state(self, name: str) -> dict[str, Any] | None:
        """Return cached state for a resource name."""
        return self._state.get(name)

    def get_resource_by_name(self, name: str) -> CoapResource | None:
        """Return the current CoapResource for the given resource name, or None."""
        for r in self.resources:
            if r.name == name:
                return r
        return None

    @callback
    def subscribe(
        self, name: str, cb: Callable[[dict[str, Any]], None]
    ) -> Callable[[], None]:
        """Subscribe to state updates for a resource name. Returns an unsubscribe callable."""
        self._subscriptions.setdefault(name, []).append(cb)

        def unsub() -> None:
            try:
                self._subscriptions[name].remove(cb)
            except (ValueError, KeyError):
                pass

        return unsub

    @callback
    def subscribe_availability(self, cb: Callable[[bool], None]) -> Callable[[], None]:
        """Subscribe to availability changes. Returns an unsubscribe callable."""
        self._availability_callbacks.append(cb)

        def unsub() -> None:
            self._availability_callbacks.remove(cb)

        return unsub

    async def async_post(
        self, path: str, payload: bytes | None = None
    ) -> dict[str, Any] | None:
        """Send a CoAP NON POST; return parsed state payload if present."""
        assert self._context is not None
        _LOGGER.debug(
            "POST %s on %s payload_len=%d",
            path,
            self.host,
            len(payload) if payload else 0,
        )
        msg = aiocoap.Message(transport_tuning=aiocoap.Unreliable, code=aiocoap.POST, uri=self._uri(path))
        if payload is not None:
            msg.payload = payload
            msg.opt.content_format = 60  # application/cbor
        response = await self._context.request(msg).response
        _LOGGER.debug(
            "POST response from %s for %s: code=%s", self.host, path, response.code
        )
        return _parse_cbor_state(response.payload) if response.payload else None

    async def async_teardown(self) -> None:
        """Cancel all observations and shut down the CoAP context."""
        _LOGGER.debug("Teardown started for %s", self.host)
        await self._async_unsubscribe_zeroconf()
        self._cancel_observations()
        if self._reconnect_task is not None:
            _LOGGER.debug("Cancelling reconnect task during teardown for %s", self.host)
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._backoff_task is not None:
            _LOGGER.debug("Cancelling backoff task during teardown for %s", self.host)
            self._backoff_task.cancel()
            self._backoff_task = None
        if self._context is not None:
            _LOGGER.debug("Shutting down aiocoap context for %s", self.host)
            await self._context.shutdown()
            self._context = None
        _LOGGER.debug("Teardown complete for %s", self.host)

    @property
    def sensors(self) -> list[CoapResource]:
        """Return all sensor resources."""
        return [r for r in self.resources if r.resource_type == RT_SENSOR]

    @property
    def switches(self) -> list[CoapResource]:
        """Return all switch resources."""
        return [r for r in self.resources if r.resource_type == RT_SWITCH]

    @property
    def binary_sensors(self) -> list[CoapResource]:
        """Return all binary sensor resources."""
        return [r for r in self.resources if r.resource_type == RT_BINARY_SENSOR]

    @property
    def buttons(self) -> list[CoapResource]:
        """Return all button resources."""
        return [r for r in self.resources if r.resource_type == RT_BUTTON]

    @property
    def text_sensors(self) -> list[CoapResource]:
        """Return all text sensor resources."""
        return [r for r in self.resources if r.resource_type == RT_TEXT_SENSOR]

    @property
    def numbers(self) -> list[CoapResource]:
        """Return all number resources."""
        return [r for r in self.resources if r.resource_type == RT_NUMBER]

    @property
    def locks(self) -> list[CoapResource]:
        """Return all lock resources."""
        return [r for r in self.resources if r.resource_type == RT_LOCK]

    @property
    def valves(self) -> list[CoapResource]:
        """Return all valve resources."""
        return [r for r in self.resources if r.resource_type == RT_VALVE]


def _parse_link_format(text: str) -> list[CoapResource]:
    """Parse RFC 6690 Link Format into CoapResource list."""
    resources: list[CoapResource] = []
    for entry in re.split(r",\s*(?=<)", text):
        entry = entry.strip()
        if not entry.startswith("<"):
            continue
        try:
            close = entry.index(">")
        except ValueError:
            continue
        path = entry[1:close].lstrip("/")
        resource = CoapResource(path=path)
        for part in entry[close + 1 :].split(";"):
            part = part.strip()
            if not part:
                continue
            if part == "obs":
                resource.observable = True
            elif "=" in part:
                key, _, val = part.partition("=")
                val = val.strip('"')
                match key:
                    case "rt":
                        resource.resource_type = val
                    case "if":
                        resource.interface = val
                    case "ct":
                        try:
                            resource.content_type = int(val)
                        except ValueError:
                            pass
                    case "title":
                        resource.title = val
                    case "oid":
                        resource.name = val
                    case "uom":
                        resource.unit = val
                    case "dc":
                        resource.device_class = val
                    case "ad":
                        try:
                            resource.accuracy_decimals = int(val)
                        except ValueError:
                            pass
                    case "dv":
                        try:
                            resource.device_index = int(val)
                        except ValueError:
                            pass
                    case "stp":
                        resource.stop_path = val.lstrip("/")
                    case "min":
                        try:
                            resource.min_value = float(val)
                        except ValueError:
                            pass
                    case "max":
                        try:
                            resource.max_value = float(val)
                        except ValueError:
                            pass
                    case "step":
                        try:
                            resource.step = float(val)
                        except ValueError:
                            pass
        if not resource.name:
            resource.name = resource.title
        resources.append(resource)
    return resources


def _parse_cbor_state(payload: bytes) -> dict[str, Any] | None:
    """Decode a CBOR entity state payload into {value, unit?}."""
    if not payload:
        return None
    try:
        raw = cbor2.loads(payload)
        record = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(record, dict):
            return None
        result: dict[str, Any] = {}
        if SENML_V in record:
            result["value"] = float(record[SENML_V])  # float() handles int too (ESPHome lock uses cbor_encode_uint)
        elif SENML_VB in record:
            result["value"] = bool(record[SENML_VB])
        elif SENML_VS in record:
            vs = record[SENML_VS]
            result["value"] = None if vs == "NA" else vs
        if SENML_U in record:
            result["unit"] = str(record[SENML_U])
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Failed to decode CBOR state payload")
        return None
    else:
        return result if "value" in result else None
