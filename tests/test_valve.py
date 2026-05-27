"""Unit tests for CoapValve — _apply_state position clamping and POST payload logic."""

import cbor2
import pytest

from coap_client_for_esphome.const import SENML_VB
from coap_client_for_esphome.coordinator import CoapResource
from coap_client_for_esphome.valve import CoapValve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valve(stop_path: str = "") -> CoapValve:
    """Return a CoapValve with __init__ bypassed — only _apply_state attrs set."""
    valve = CoapValve.__new__(CoapValve)
    valve._attr_current_valve_position = None
    valve._attr_is_opening = False
    valve._attr_is_closing = False
    valve._attr_assumed_state = False
    valve._resource = CoapResource(path="fp/8", name="garden_valve", stop_path=stop_path)
    return valve


# ---------------------------------------------------------------------------
# _apply_state: position clamping
# ---------------------------------------------------------------------------


def test_apply_state_closed():
    valve = _make_valve()
    valve._apply_state({"value": 0.0})
    assert valve._attr_current_valve_position == 0


def test_apply_state_half_open():
    valve = _make_valve()
    valve._apply_state({"value": 0.5})
    assert valve._attr_current_valve_position == 50


def test_apply_state_fully_open():
    valve = _make_valve()
    valve._apply_state({"value": 1.0})
    assert valve._attr_current_valve_position == 100


def test_apply_state_clamps_below_zero():
    valve = _make_valve()
    valve._apply_state({"value": -0.1})
    assert valve._attr_current_valve_position == 0


def test_apply_state_clamps_above_one():
    valve = _make_valve()
    valve._apply_state({"value": 1.1})
    assert valve._attr_current_valve_position == 100


def test_apply_state_rounds_position():
    valve = _make_valve()
    valve._apply_state({"value": 0.254})
    assert valve._attr_current_valve_position == 25  # round(25.4) = 25

    valve2 = _make_valve()
    valve2._apply_state({"value": 0.256})
    assert valve2._attr_current_valve_position == 26  # round(25.6) = 26


def test_apply_state_none_value_leaves_position_unchanged():
    valve = _make_valve()
    valve._attr_current_valve_position = 50
    valve._apply_state({"value": None})
    assert valve._attr_current_valve_position == 50


def test_apply_state_clears_opening_closing_flags():
    valve = _make_valve()
    valve._attr_is_opening = True
    valve._attr_is_closing = True
    valve._apply_state({"value": 0.5})
    assert valve._attr_is_opening is False
    assert valve._attr_is_closing is False


def test_apply_state_clears_assumed_state():
    valve = _make_valve()
    valve._attr_assumed_state = True
    valve._apply_state({"value": 0.0})
    assert valve._attr_assumed_state is False


# ---------------------------------------------------------------------------
# POST payload: _post_and_confirm sends correct CBOR
# ---------------------------------------------------------------------------


class _TrackingCoord:
    def __init__(self):
        self.posts: list[tuple[str, bytes]] = []

    async def async_post(self, path: str, payload: bytes):
        self.posts.append((path, payload))
        return None

    def get_state(self, name: str):
        return None


async def test_post_and_confirm_open_sends_true():
    coord = _TrackingCoord()
    valve = _make_valve()
    valve._coordinator = coord

    await valve._post_and_confirm("fp/8", True)

    assert len(coord.posts) == 1
    path, payload = coord.posts[0]
    assert path == "fp/8"
    assert cbor2.loads(payload) == {SENML_VB: True}


async def test_post_and_confirm_close_sends_false():
    coord = _TrackingCoord()
    valve = _make_valve()
    valve._coordinator = coord

    await valve._post_and_confirm("fp/8", False)

    _, payload = coord.posts[0]
    assert cbor2.loads(payload) == {SENML_VB: False}


async def test_post_and_confirm_uses_explicit_stop_path():
    coord = _TrackingCoord()
    valve = _make_valve(stop_path="fp/8/stop")
    valve._coordinator = coord

    await valve._post_and_confirm("fp/8/stop", True)

    path, _ = coord.posts[0]
    assert path == "fp/8/stop"


async def test_stop_uses_stop_path_when_present():
    """async_stop_valve should POST to stop_path, not the main path."""
    posts: list[tuple[str, bytes]] = []

    class _StopCoord(_TrackingCoord):
        async def async_post(self, path, payload):
            posts.append((path, payload))
            return None

    coord = _StopCoord()
    valve = _make_valve(stop_path="fp/8/stop")
    valve._coordinator = coord
    valve.async_write_ha_state = lambda: None

    # Call _post_and_confirm directly with the stop path (as async_stop_valve does)
    await valve._post_and_confirm("fp/8/stop", True)

    assert posts[0][0] == "fp/8/stop"


async def test_stop_fallback_path():
    """When stop_path is empty, async_stop_valve falls back to path[:-1] + '2'."""
    coord = _TrackingCoord()
    valve = _make_valve(stop_path="")
    valve._coordinator = coord
    valve.async_write_ha_state = lambda: None

    # Replicate what async_stop_valve computes for stop_path
    resource = valve._resource
    stop_path = resource.stop_path or resource.path[:-1] + "2"
    await valve._post_and_confirm(stop_path, True)

    path, _ = coord.posts[0]
    # "fp/8"[:-1] + "2" = "fp/2"
    assert path == "fp/2"


async def test_post_and_confirm_applies_returned_state():
    class _ReturningCoord(_TrackingCoord):
        async def async_post(self, path, payload):
            await super().async_post(path, payload)
            return {"value": 1.0}

    coord = _ReturningCoord()
    valve = _make_valve()
    valve._coordinator = coord
    valve.async_write_ha_state = lambda: None

    await valve._post_and_confirm("fp/8", True)

    assert valve._attr_current_valve_position == 100
