"""Tests for observation, notification delivery, availability, and ping loop."""

import asyncio
from unittest.mock import patch

import pytest

from coap_client_for_esphome.coordinator import CoapCoordinator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _start_and_settle(coordinator, delay: float = 0.2) -> None:
    """Setup coordinator, start observations, and give the event loop time to run."""
    await coordinator.async_setup()
    coordinator.async_start_observations()
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Initial value delivery
# ---------------------------------------------------------------------------


async def test_initial_value_delivered_sensor(coordinator, mock_server):
    mock_server.set_value("temperature", 23.5)
    await _start_and_settle(coordinator)
    state = coordinator.get_state("temperature")
    assert state is not None
    assert state["value"] == 23.5


async def test_initial_value_delivered_binary_sensor(coordinator, mock_server):
    mock_server.set_value("motion", True)
    await _start_and_settle(coordinator)
    state = coordinator.get_state("motion")
    assert state is not None
    assert state["value"] is True


async def test_initial_value_delivered_switch(coordinator, mock_server):
    mock_server.set_value("relay", False)
    await _start_and_settle(coordinator)
    state = coordinator.get_state("relay")
    assert state is not None
    assert state["value"] is False


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


async def test_available_after_initial_response(coordinator, mock_server):
    await _start_and_settle(coordinator)
    assert coordinator.available is True


async def test_availability_callback_fired(coordinator, mock_server):
    changes: list[bool] = []
    coordinator.subscribe_availability(lambda v: changes.append(v))
    await _start_and_settle(coordinator)
    assert True in changes


# ---------------------------------------------------------------------------
# Notification delivery
# ---------------------------------------------------------------------------


async def test_notification_updates_sensor_state(coordinator, mock_server):
    await _start_and_settle(coordinator)
    mock_server.set_value("temperature", 30.0)
    await asyncio.sleep(0.2)
    assert coordinator.get_state("temperature")["value"] == 30.0


async def test_notification_updates_binary_sensor_state(coordinator, mock_server):
    await _start_and_settle(coordinator)
    mock_server.set_value("motion", True)
    await asyncio.sleep(0.2)
    assert coordinator.get_state("motion")["value"] is True


async def test_multiple_notifications_all_delivered(coordinator, mock_server):
    await _start_and_settle(coordinator)
    for v in [10.0, 20.0, 30.0]:
        mock_server.set_value("temperature", v)
        await asyncio.sleep(0.1)
    assert coordinator.get_state("temperature")["value"] == 30.0


# ---------------------------------------------------------------------------
# Subscription callbacks
# ---------------------------------------------------------------------------


async def test_subscribe_callback_fires_on_notification(coordinator, mock_server):
    received: list = []
    await coordinator.async_setup()
    coordinator.subscribe("temperature", lambda d: received.append(d["value"]))
    coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    mock_server.set_value("temperature", 42.0)
    await asyncio.sleep(0.2)
    assert 42.0 in received


async def test_subscribe_callback_fires_on_initial_value(coordinator, mock_server):
    received: list = []
    mock_server.set_value("temperature", 11.0)
    await coordinator.async_setup()
    coordinator.subscribe("temperature", lambda d: received.append(d["value"]))
    coordinator.async_start_observations()
    await asyncio.sleep(0.2)
    assert 11.0 in received


async def test_unsubscribe_stops_callbacks(coordinator, mock_server):
    received: list = []
    await coordinator.async_setup()
    unsub = coordinator.subscribe("temperature", lambda d: received.append(d["value"]))
    coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    unsub()
    count_before = len(received)
    mock_server.set_value("temperature", 99.0)
    await asyncio.sleep(0.2)
    assert len(received) == count_before  # no new callbacks after unsub


# ---------------------------------------------------------------------------
# Log resource observe gating
# ---------------------------------------------------------------------------


async def test_log_resource_not_observed_by_default(coordinator, mock_server):
    await coordinator.async_setup()
    coordinator.async_start_observations()
    await asyncio.sleep(0.2)
    # With subscribe_logs=False, no task is created for the log resource
    task_names = [t.get_name() for t in coordinator._observe_tasks]
    assert not any("log" in name for name in task_names)


async def test_log_resource_observed_when_enabled(hass, mock_server):
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        subscribe_logs=True,
    )
    try:
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)
        task_names = [t.get_name() for t in coord._observe_tasks]
        assert any("log" in name for name in task_names)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# get_state / get_resource_by_name
# ---------------------------------------------------------------------------


async def test_get_state_returns_none_before_first_delivery(coordinator):
    await coordinator.async_setup()
    assert coordinator.get_state("temperature") is None


async def test_get_resource_by_name_returns_resource(coordinator):
    await coordinator.async_setup()
    r = coordinator.get_resource_by_name("temperature")
    assert r is not None
    assert r.path == "fp/1"


async def test_get_resource_by_name_missing_returns_none(coordinator):
    await coordinator.async_setup()
    assert coordinator.get_resource_by_name("nonexistent") is None


# ---------------------------------------------------------------------------
# Lock and valve notification delivery
# ---------------------------------------------------------------------------


async def test_initial_value_delivered_lock(lock_valve_coordinator, lock_valve_server):
    lock_valve_server.set_value("door_lock", 1)  # LOCKED
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.2)
    state = lock_valve_coordinator.get_state("door_lock")
    assert state is not None
    assert state["value"] == 1


async def test_initial_value_delivered_valve(lock_valve_coordinator, lock_valve_server):
    lock_valve_server.set_value("garden_valve", 0.5)
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.5)  # valve is index 4; stagger=0.2s, needs extra headroom
    state = lock_valve_coordinator.get_state("garden_valve")
    assert state is not None
    assert state["value"] == pytest.approx(0.5)


async def test_notification_updates_lock_state(lock_valve_coordinator, lock_valve_server):
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    lock_valve_server.set_value("door_lock", 2)  # UNLOCKED
    await asyncio.sleep(0.2)
    assert lock_valve_coordinator.get_state("door_lock")["value"] == 2


async def test_notification_updates_valve_position(lock_valve_coordinator, lock_valve_server):
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    lock_valve_server.set_value("garden_valve", 1.0)
    await asyncio.sleep(0.2)
    assert lock_valve_coordinator.get_state("garden_valve")["value"] == pytest.approx(1.0)


async def test_lock_subscribe_callback_fires(lock_valve_coordinator, lock_valve_server):
    received: list = []
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.subscribe("door_lock", lambda d: received.append(d["value"]))
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    lock_valve_server.set_value("door_lock", 3)  # JAMMED
    await asyncio.sleep(0.2)
    assert 3 in received


# ---------------------------------------------------------------------------
# Callback exception isolation
# ---------------------------------------------------------------------------


async def test_failing_callback_does_not_block_other_subscribers(coordinator, mock_server):
    """A callback that raises must not prevent other subscribers from receiving state."""
    received: list = []

    def _bad(_data):
        raise RuntimeError("intentional failure in callback")

    await coordinator.async_setup()
    coordinator.subscribe("temperature", _bad)
    coordinator.subscribe("temperature", lambda d: received.append(d["value"]))
    coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    mock_server.set_value("temperature", 42.0)
    await asyncio.sleep(0.2)
    assert 42.0 in received


async def test_valve_subscribe_callback_fires(lock_valve_coordinator, lock_valve_server):
    received: list = []
    await lock_valve_coordinator.async_setup()
    lock_valve_coordinator.subscribe("garden_valve", lambda d: received.append(d["value"]))
    lock_valve_coordinator.async_start_observations()
    await asyncio.sleep(0.2)

    lock_valve_server.set_value("garden_valve", 0.75)
    await asyncio.sleep(0.2)
    assert any(abs(v - 0.75) < 0.01 for v in received)


# ---------------------------------------------------------------------------
# Subscribe jitter
# ---------------------------------------------------------------------------


async def test_start_observe_tasks_calls_subscribe_jitter(coordinator, mock_server):
    """_start_observe_tasks calls _subscribe_jitter_s() once per invocation."""
    await coordinator.async_setup()
    with patch.object(coordinator, "_subscribe_jitter_s", return_value=0.0) as mock_jitter:
        coordinator._start_observe_tasks()
    mock_jitter.assert_called_once_with()


async def test_observe_stagger_includes_jitter(coordinator, mock_server):
    """Each entity's stagger_s is jitter_base + index * 0.05."""
    await coordinator.async_setup()
    staggers: list[float] = []

    async def _capturing_observe(resource, stagger_s: float = 0.0):
        staggers.append(stagger_s)

    with patch.object(coordinator, "_subscribe_jitter_s", return_value=0.3), \
         patch.object(coordinator, "_async_observe", side_effect=_capturing_observe):
        coordinator._start_observe_tasks()
        await asyncio.sleep(0)

    # DEFAULT_LINK_FORMAT has 3 non-log observable resources (temperature, motion, relay)
    # at enumeration indices 0, 1, 2 — logs are index 3 but skipped when subscribe_logs=False
    assert len(staggers) == 3
    assert staggers[0] == pytest.approx(0.30)
    assert staggers[1] == pytest.approx(0.35)
    assert staggers[2] == pytest.approx(0.40)


async def test_resubscribe_also_jitters(coordinator, mock_server):
    """async_resubscribe draws a fresh jitter so simultaneous reconnects stay spread."""
    await _start_and_settle(coordinator)
    staggers: list[float] = []

    async def _capturing_observe(resource, stagger_s: float = 0.0):
        staggers.append(stagger_s)

    with patch.object(coordinator, "_subscribe_jitter_s", return_value=0.25), \
         patch.object(coordinator, "_async_observe", side_effect=_capturing_observe):
        coordinator.async_resubscribe()
        await asyncio.sleep(0)

    assert len(staggers) == 3
    assert staggers[0] == pytest.approx(0.25)
