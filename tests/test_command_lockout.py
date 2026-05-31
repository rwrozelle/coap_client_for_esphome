"""Tests for the 1-second command lockout on action entities."""

import pytest

from coap_client_for_esphome.coordinator import CoapResource
from coap_client_for_esphome.lock import CoapLock
from coap_client_for_esphome.number import CoapNumber
from coap_client_for_esphome.switch import CoapSwitch
from coap_client_for_esphome.valve import CoapValve


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeHass:
    """Minimal hass stub that tracks background task spawns."""

    def __init__(self, now: float = 0.0):
        self._now = now
        self.tasks_created: int = 0

    def loop_time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def async_create_background_task(self, coro, *, name=""):
        self.tasks_created += 1
        coro.close()  # prevent coroutine-never-awaited warning


class _FakeLoop:
    def __init__(self, hass: _FakeHass):
        self._hass = hass

    def time(self) -> float:
        return self._hass.loop_time()


def _attach_hass(entity, now: float = 0.0) -> _FakeHass:
    hass = _FakeHass(now)
    entity.hass = type("H", (), {
        "loop": _FakeLoop(hass),
        "async_create_background_task": lambda self_, coro, *, name="": hass.async_create_background_task(coro, name=name),
    })()
    entity.async_write_ha_state = lambda: None
    return hass


def _make_switch() -> CoapSwitch:
    sw = CoapSwitch.__new__(CoapSwitch)
    sw._attr_is_on = False
    sw._attr_assumed_state = False
    sw._locked_until = 0.0
    sw._resource = CoapResource(path="fp/1/g/1", name="sw")
    return sw


def _make_lock() -> CoapLock:
    lk = CoapLock.__new__(CoapLock)
    lk._attr_is_locked = None
    lk._attr_is_locking = False
    lk._attr_is_unlocking = False
    lk._attr_is_jammed = False
    lk._attr_is_open = False
    lk._attr_is_opening = False
    lk._attr_assumed_state = False
    lk._locked_until = 0.0
    lk._resource = CoapResource(path="fp/2/g/1", name="lk")
    return lk


def _make_valve() -> CoapValve:
    vl = CoapValve.__new__(CoapValve)
    vl._attr_current_valve_position = 0
    vl._attr_is_opening = False
    vl._attr_is_closing = False
    vl._attr_assumed_state = False
    vl._locked_until = 0.0
    vl._resource = CoapResource(path="fp/3/g/1", name="vl")
    return vl


def _make_number() -> CoapNumber:
    nb = CoapNumber.__new__(CoapNumber)
    nb._attr_native_value = 0.0
    nb._attr_assumed_state = False
    nb._locked_until = 0.0
    nb._resource = CoapResource(path="fp/4/g/1", name="nb")
    return nb


# ---------------------------------------------------------------------------
# Switch lockout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_switch_first_command_spawns_task():
    sw = _make_switch()
    hass = _attach_hass(sw, now=0.0)
    await sw.async_turn_on()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_switch_second_command_within_lockout_is_dropped():
    sw = _make_switch()
    hass = _attach_hass(sw, now=0.0)
    await sw.async_turn_on()
    await sw.async_turn_off()
    assert hass.tasks_created == 1  # second command dropped


@pytest.mark.asyncio
async def test_switch_command_allowed_after_lockout_expires():
    sw = _make_switch()
    hass = _attach_hass(sw, now=0.0)
    await sw.async_turn_on()
    hass.advance(1.001)
    await sw.async_turn_off()
    assert hass.tasks_created == 2


# ---------------------------------------------------------------------------
# Lock lockout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_lock_first_command_spawns_task():
    lk = _make_lock()
    hass = _attach_hass(lk, now=0.0)
    await lk.async_lock()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_lock_second_command_within_lockout_is_dropped():
    lk = _make_lock()
    hass = _attach_hass(lk, now=0.0)
    await lk.async_lock()
    await lk.async_unlock()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_lock_command_allowed_after_lockout_expires():
    lk = _make_lock()
    hass = _attach_hass(lk, now=0.0)
    await lk.async_lock()
    hass.advance(1.001)
    await lk.async_unlock()
    assert hass.tasks_created == 2


# ---------------------------------------------------------------------------
# Valve lockout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valve_first_command_spawns_task():
    vl = _make_valve()
    hass = _attach_hass(vl, now=0.0)
    await vl.async_open_valve()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_valve_second_command_within_lockout_is_dropped():
    vl = _make_valve()
    hass = _attach_hass(vl, now=0.0)
    await vl.async_open_valve()
    await vl.async_close_valve()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_valve_stop_within_lockout_is_dropped():
    vl = _make_valve()
    hass = _attach_hass(vl, now=0.0)
    await vl.async_open_valve()
    await vl.async_stop_valve()
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_valve_command_allowed_after_lockout_expires():
    vl = _make_valve()
    hass = _attach_hass(vl, now=0.0)
    await vl.async_open_valve()
    hass.advance(1.001)
    await vl.async_close_valve()
    assert hass.tasks_created == 2


# ---------------------------------------------------------------------------
# Number lockout
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_number_first_command_spawns_task():
    nb = _make_number()
    hass = _attach_hass(nb, now=0.0)
    await nb.async_set_native_value(50.0)
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_number_second_command_within_lockout_is_dropped():
    nb = _make_number()
    hass = _attach_hass(nb, now=0.0)
    await nb.async_set_native_value(50.0)
    await nb.async_set_native_value(75.0)
    assert hass.tasks_created == 1


@pytest.mark.asyncio
async def test_number_command_allowed_after_lockout_expires():
    nb = _make_number()
    hass = _attach_hass(nb, now=0.0)
    await nb.async_set_native_value(50.0)
    hass.advance(1.001)
    await nb.async_set_native_value(75.0)
    assert hass.tasks_created == 2
