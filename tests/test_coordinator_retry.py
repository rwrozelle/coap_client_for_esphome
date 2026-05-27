"""Tests for observe retry (observe_max_retries) and subscription_confirm behavior."""

import asyncio
import socket

import aiocoap
import aiocoap.resource as resource
import cbor2
import pytest

from coap_client_for_esphome.coordinator import CoapCoordinator
from coap_client_for_esphome.const import SENML_V

_SERVER_HOST = "127.0.0.1"

_DEVICE_INFO = {
    "name": "test_device",
    "friendly_name": "Test Device",
    "version": "1.0.0",
    "build_time": "2024-01-01",
    "model": "ESP32-C6",
    "ping_interval": 60,
    "ping_timeout": 10,
    "ping_retry": 1,
    "areas": [],
    "devices": [
        {"name": "test_device", "friendly_name": "Test Device", "model": "ESP32-C6"}
    ],
}


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((_SERVER_HOST, 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Helper resources
# ---------------------------------------------------------------------------


class _TerminatingEntityResource(resource.ObservableResource):
    """Observable resource that can terminate all active observations on demand."""

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
            code=aiocoap.CONTENT, payload=self._payload(), content_format=60
        )

    def end_all_observations(self) -> None:
        """Server-initiated final notification; terminates client-side iteration."""
        for obs in list(self._observations):
            obs.trigger(is_last=True)

    def push_notification(self) -> None:
        """Send current value to all active observers (normal update, not last)."""
        self.updated_state()


class _MtypeCapturingEntityResource(resource.ObservableResource):
    """Observable resource that records the mtype of each incoming observe request."""

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
            code=aiocoap.CONTENT, payload=self._payload(), content_format=60
        )


# ---------------------------------------------------------------------------
# Minimal server builder
# ---------------------------------------------------------------------------


async def _make_server(sensor_resource, subscription_confirm: bool = False, observe_retry: int = 0):
    """Return (aiocoap_context, host, port) for a minimal one-sensor server."""
    site = resource.Site()
    port = _free_udp_port()
    link_format = (
        '</fp/1>;rt="esphome.sensor";obs;oid="temperature",'
        '</ping>;rt="esphome.ping",'
        '</info>;rt="esphome.device"'
    )
    info = {**_DEVICE_INFO, "subscription_confirm": subscription_confirm, "observe_retry": observe_retry}

    class _InfoRes(resource.Resource):
        async def render_get(self, _request):
            return aiocoap.Message(
                code=aiocoap.CONTENT, payload=cbor2.dumps(info), content_format=60
            )

    class _PingRes(resource.Resource):
        async def render_get(self, _request):
            return aiocoap.Message(mtype=aiocoap.NON, code=aiocoap.CONTENT, payload=b"")

    class _WKCRes(resource.Resource):
        async def render_get(self, _request):
            return aiocoap.Message(
                code=aiocoap.CONTENT, payload=link_format.encode(), content_format=40
            )

    site.add_resource(["fp", "1"], sensor_resource)
    site.add_resource(["ping"], _PingRes())
    site.add_resource(["info"], _InfoRes())
    site.add_resource([".well-known", "core"], _WKCRes())

    ctx = await aiocoap.Context.create_server_context(site, bind=(_SERVER_HOST, port))
    return ctx, _SERVER_HOST, port


# ---------------------------------------------------------------------------
# Default behavior: no retry — updates stop after stream ends
# ---------------------------------------------------------------------------


async def test_observe_default_no_retry_stops_updates(hass):
    """With observe_max_retries=0 (default), updates stop permanently after stream ends."""
    sensor = _TerminatingEntityResource(value=1.0)
    ctx, host, port = await _make_server(sensor)
    try:
        coord = CoapCoordinator(hass=hass, host=host, port=port)
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        assert coord.get_state("temperature")["value"] == 1.0

        # End observation; the coordinator receives 1.0 as the last notification
        sensor.end_all_observations()
        await asyncio.sleep(0.2)

        # Change value and push a new notification
        sensor._value = 99.0
        sensor.push_notification()
        await asyncio.sleep(0.2)

        # Without retry, state is not updated to 99.0
        assert coord.get_state("temperature")["value"] != 99.0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()


# ---------------------------------------------------------------------------
# Retry on stream end — updates resume after retry
# ---------------------------------------------------------------------------


async def test_observe_retries_resume_updates(hass):
    """With observe_retry>=1 in /info, coordinator re-subscribes and resumes updates."""
    sensor = _TerminatingEntityResource(value=2.0)
    ctx, host, port = await _make_server(sensor, observe_retry=2)
    try:
        coord = CoapCoordinator(
            hass=hass,
            host=host,
            port=port,
            observe_retry_initial_delay_s=0.05,
        )
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        # End observation and wait for coordinator to retry
        sensor.end_all_observations()
        await asyncio.sleep(0.3)  # retry delay (0.05) + re-subscribe time

        # Push new value; coordinator should receive it via the retried observation
        sensor._value = 77.0
        sensor.push_notification()
        await asyncio.sleep(0.2)

        assert coord.get_state("temperature")["value"] == 77.0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()


# ---------------------------------------------------------------------------
# Unavailable after exhausting retries
# ---------------------------------------------------------------------------


async def test_observe_unavailable_after_max_retries(hass):
    """Coordinator marks itself unavailable after all retries are exhausted."""
    sensor = _TerminatingEntityResource(value=4.0)
    ctx, host, port = await _make_server(sensor, observe_retry=1)
    try:
        coord = CoapCoordinator(
            hass=hass,
            host=host,
            port=port,
            observe_retry_initial_delay_s=0.05,
        )
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        assert coord.available is True

        # End initial observation → coordinator retries (attempt 1/1)
        sensor.end_all_observations()
        await asyncio.sleep(0.3)  # allow retry to establish

        # End retry observation → retries exhausted → unavailable
        sensor.end_all_observations()
        await asyncio.sleep(0.3)

        assert coord.available is False
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()


# ---------------------------------------------------------------------------
# Availability callback fires when unavailable after retries
# ---------------------------------------------------------------------------


async def test_unavailable_callback_fires_after_retries_exhausted(hass):
    """The availability callback fires with False after all retries are exhausted."""
    sensor = _TerminatingEntityResource(value=5.0)
    ctx, host, port = await _make_server(sensor, observe_retry=1)
    try:
        availability_events: list[bool] = []
        coord = CoapCoordinator(
            hass=hass,
            host=host,
            port=port,
            observe_retry_initial_delay_s=0.05,
        )
        coord.subscribe_availability(lambda v: availability_events.append(v))
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        sensor.end_all_observations()
        await asyncio.sleep(0.3)
        sensor.end_all_observations()
        await asyncio.sleep(0.3)

        assert False in availability_events
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()


# ---------------------------------------------------------------------------
# subscription_confirm: CON vs NON
# ---------------------------------------------------------------------------


async def test_subscription_confirm_false_sends_non(hass):
    """subscription_confirm=false in /info causes NON observe requests."""
    sensor = _MtypeCapturingEntityResource(value=0.0)
    ctx, host, port = await _make_server(sensor, subscription_confirm=False)
    try:
        coord = CoapCoordinator(hass=hass, host=host, port=port)
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        assert len(sensor.received_mtypes) >= 1
        # The coordinator's own observe request is NON
        assert sensor.received_mtypes[0] == aiocoap.NON
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()


async def test_subscription_confirm_true_sends_con(hass):
    """subscription_confirm=true in /info causes CON observe requests."""
    sensor = _MtypeCapturingEntityResource(value=0.0)
    ctx, host, port = await _make_server(sensor, subscription_confirm=True)
    try:
        coord = CoapCoordinator(hass=hass, host=host, port=port)
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        assert len(sensor.received_mtypes) >= 1
        # The coordinator's own observe request is CON
        assert sensor.received_mtypes[0] == aiocoap.CON
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await ctx.shutdown()
