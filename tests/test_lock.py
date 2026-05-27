"""Unit tests for CoapLock — _apply_state mappings and POST payload logic."""

import cbor2
import pytest

from coap_client_for_esphome.const import SENML_VB
from coap_client_for_esphome.coordinator import CoapResource
from coap_client_for_esphome.lock import CoapLock, _ESPHOME_TO_HA_LOCK_STATE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lock() -> CoapLock:
    """Return a CoapLock with __init__ bypassed — only _apply_state attrs set."""
    lock = CoapLock.__new__(CoapLock)
    lock._attr_is_locked = None
    lock._attr_is_locking = False
    lock._attr_is_unlocking = False
    lock._attr_is_jammed = False
    lock._attr_is_open = False
    lock._attr_is_opening = False
    lock._attr_assumed_state = False
    return lock


# ---------------------------------------------------------------------------
# _apply_state: all 8 ESPHome LockState values
# ---------------------------------------------------------------------------


def test_apply_state_none_state():
    lock = _make_lock()
    lock._apply_state({"value": 0})
    assert lock._attr_is_locked is None
    assert lock._attr_is_locking is False
    assert lock._attr_is_unlocking is False
    assert lock._attr_is_jammed is False
    assert lock._attr_is_open is False
    assert lock._attr_is_opening is False
    assert lock._attr_assumed_state is False


def test_apply_state_locked():
    lock = _make_lock()
    lock._apply_state({"value": 1})
    assert lock._attr_is_locked is True
    assert lock._attr_is_locking is False
    assert lock._attr_is_unlocking is False
    assert lock._attr_is_jammed is False


def test_apply_state_unlocked():
    lock = _make_lock()
    lock._apply_state({"value": 2})
    assert lock._attr_is_locked is False
    assert lock._attr_is_locking is False
    assert lock._attr_is_unlocking is False
    assert lock._attr_is_jammed is False


def test_apply_state_jammed():
    lock = _make_lock()
    lock._apply_state({"value": 3})
    assert lock._attr_is_locked is False
    assert lock._attr_is_jammed is True
    assert lock._attr_is_locking is False
    assert lock._attr_is_unlocking is False


def test_apply_state_locking():
    lock = _make_lock()
    lock._apply_state({"value": 4})
    assert lock._attr_is_locked is False
    assert lock._attr_is_locking is True
    assert lock._attr_is_unlocking is False


def test_apply_state_unlocking():
    lock = _make_lock()
    lock._apply_state({"value": 5})
    assert lock._attr_is_locked is False
    assert lock._attr_is_unlocking is True
    assert lock._attr_is_locking is False


def test_apply_state_opening():
    lock = _make_lock()
    lock._apply_state({"value": 6})
    assert lock._attr_is_locked is False
    assert lock._attr_is_opening is True
    assert lock._attr_is_open is False


def test_apply_state_open():
    lock = _make_lock()
    lock._apply_state({"value": 7})
    assert lock._attr_is_locked is False
    assert lock._attr_is_open is True
    assert lock._attr_is_opening is False


def test_apply_state_no_value_leaves_lock_unchanged():
    lock = _make_lock()
    lock._attr_is_locked = True
    lock._apply_state({"value": None})
    # None raw → ha_state is None → _attr_is_locked stays None (reset)
    assert lock._attr_is_locked is None


def test_apply_state_clears_assumed_state():
    lock = _make_lock()
    lock._attr_assumed_state = True
    lock._apply_state({"value": 1})
    assert lock._attr_assumed_state is False


def test_apply_state_float_value_also_works():
    # Server might encode as float due to SENML_V type — int() handles both
    lock = _make_lock()
    lock._apply_state({"value": 1.0})
    assert lock._attr_is_locked is True


# ---------------------------------------------------------------------------
# esphome→HA mapping table completeness
# ---------------------------------------------------------------------------


def test_esphome_lock_state_map_covers_all_states():
    assert set(_ESPHOME_TO_HA_LOCK_STATE.keys()) == {0, 1, 2, 3, 4, 5, 6, 7}


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


async def test_post_and_confirm_lock_sends_true():
    coord = _TrackingCoord()
    lock = _make_lock()
    lock._coordinator = coord
    lock._resource = CoapResource(path="fp/7", name="door_lock")

    await lock._post_and_confirm("fp/7", True)

    assert len(coord.posts) == 1
    path, payload = coord.posts[0]
    assert path == "fp/7"
    assert cbor2.loads(payload) == {SENML_VB: True}


async def test_post_and_confirm_unlock_sends_false():
    coord = _TrackingCoord()
    lock = _make_lock()
    lock._coordinator = coord
    lock._resource = CoapResource(path="fp/7", name="door_lock")

    await lock._post_and_confirm("fp/7", False)

    assert len(coord.posts) == 1
    _, payload = coord.posts[0]
    assert cbor2.loads(payload) == {SENML_VB: False}


async def test_post_and_confirm_applies_returned_state():
    class _ReturningCoord(_TrackingCoord):
        async def async_post(self, path, payload):
            await super().async_post(path, payload)
            return {"value": 1}  # server confirms LOCKED

    coord = _ReturningCoord()
    lock = _make_lock()
    lock._coordinator = coord
    lock._resource = CoapResource(path="fp/7", name="door_lock")
    lock.async_write_ha_state = lambda: None

    await lock._post_and_confirm("fp/7", True)

    assert lock._attr_is_locked is True
