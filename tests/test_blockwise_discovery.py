"""Tests for Block2 (RFC 7959) transparent reassembly in .well-known/core discovery.

The coordinator sends Block2(0, False, 6) as a capability hint; the server decides
the actual SZX in its response.  aiocoap's Block2Cache (server-side) chunks the
full payload at block_size = 16 << SZX bytes; aiocoap's client-side
_complete_by_requesting_block2 reassembles blocks transparently.

CoapCoordinator needs no additional logic — these tests verify end-to-end.
"""

import asyncio
import socket

import aiocoap
import aiocoap.optiontypes
import aiocoap.resource as resource
import cbor2
import pytest

from coap_client_for_esphome.coordinator import CoapCoordinator

_SERVER_HOST = "127.0.0.1"
_SZX = 6
_BLOCK_SIZE = 16 << _SZX  # 1024 bytes — server's chosen block size


def _free_udp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind((_SERVER_HOST, 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Mock resources
# ---------------------------------------------------------------------------


class _BlockWKCResource(resource.Resource):
    """Returns the full link-format payload.

    aiocoap's Block2Cache chunks it at _BLOCK_SIZE bytes when the coordinator's
    Block2(0, False, SZX) hint is present in the request.
    """

    def __init__(self, link_format: str) -> None:
        super().__init__()
        self._data = link_format.encode()

    async def render_get(self, request):
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=self._data,
            content_format=40,
        )


class _InfoResource(resource.Resource):
    async def render_get(self, request):
        return aiocoap.Message(
            code=aiocoap.CONTENT,
            payload=cbor2.dumps(
                {
                    "name": "test_device",
                    "friendly_name": "Test Device",
                    "version": "1.0.0",
                    "build_time": "2025-01-01",
                    "model": "ESP32-C6",
                    "ping_interval": 60,
                    "ping_timeout": 10,
                    "ping_retry": 1,
                    "areas": [],
                    "devices": [],
                }
            ),
            content_format=60,
        )


class _BlockwiseDiscoveryServer:
    """Minimal CoAP server with a block-wise .well-known/core and /info."""

    def __init__(self, link_format: str) -> None:
        self._port = _free_udp_port()
        self._site = resource.Site()
        self._site.add_resource([".well-known", "core"], _BlockWKCResource(link_format))
        self._site.add_resource(["info"], _InfoResource())
        self._context: aiocoap.Context | None = None

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


# ---------------------------------------------------------------------------
# Link-format helpers
# ---------------------------------------------------------------------------

# 53 bytes — fits in one block; server returns without Block2
_SINGLE_BLOCK_LF = '</info>;rt="esphome.device",</ping>;rt="esphome.ping"'


def _make_lf(n_sensors: int) -> str:
    """n non-observable sensor entries + /info + /ping.

    Non-observable so no observe tasks are started during setup.
    """
    parts = [f'</fp/{i}>;rt="esphome.sensor";oid="s{i}"' for i in range(1, n_sensors + 1)]
    parts += ['</info>;rt="esphome.device"', '</ping>;rt="esphome.ping"']
    return ",".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_block2_single_block_all_resources_found(hass):
    """Payload fits in one block; server returns without Block2."""
    assert len(_SINGLE_BLOCK_LF.encode()) < _BLOCK_SIZE

    server = _BlockwiseDiscoveryServer(_SINGLE_BLOCK_LF)
    await server.start()
    coord = CoapCoordinator(hass=hass, host=server.host, port=server.port)
    try:
        await coord.async_setup()
        paths = [r.path for r in coord.resources]
        assert "info" in paths
        assert "ping" in paths
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


async def test_block2_two_blocks_all_sensors_found(hass):
    """Payload spanning 2 blocks is reassembled; all sensors discovered."""
    lf = _make_lf(28)
    data = lf.encode()
    n_blocks = (len(data) + _BLOCK_SIZE - 1) // _BLOCK_SIZE
    assert n_blocks == 2, f"expected 2 blocks, got {n_blocks} for {len(data)} bytes"

    server = _BlockwiseDiscoveryServer(lf)
    await server.start()
    coord = CoapCoordinator(hass=hass, host=server.host, port=server.port)
    try:
        await coord.async_setup()
        sensor_paths = [r for r in coord.resources if r.resource_type == "esphome.sensor"]
        assert len(sensor_paths) == 28
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


async def test_block2_many_blocks_all_sensors_found(hass):
    """Payload spanning 3+ blocks is fully reassembled."""
    lf = _make_lf(60)
    data = lf.encode()
    n_blocks = (len(data) + _BLOCK_SIZE - 1) // _BLOCK_SIZE
    assert n_blocks >= 3, f"expected ≥3 blocks, got {n_blocks}"

    server = _BlockwiseDiscoveryServer(lf)
    await server.start()
    coord = CoapCoordinator(hass=hass, host=server.host, port=server.port)
    try:
        await coord.async_setup()
        sensor_resources = [r for r in coord.resources if r.resource_type == "esphome.sensor"]
        assert len(sensor_resources) == 60
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


async def test_block2_payload_boundary_not_on_entry_boundary(hass):
    """Block boundary mid-entry: reassembled text still parses correctly."""
    lf = _make_lf(28)
    data = lf.encode()
    # Verify the first block cuts inside an entry (not at a comma boundary)
    assert b"," not in data[_BLOCK_SIZE - 4 : _BLOCK_SIZE]

    server = _BlockwiseDiscoveryServer(lf)
    await server.start()
    coord = CoapCoordinator(hass=hass, host=server.host, port=server.port)
    try:
        await coord.async_setup()
        sensor_resources = [r for r in coord.resources if r.resource_type == "esphome.sensor"]
        assert len(sensor_resources) == 28
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()
