"""Tests for periodic and manual resubscription of CoAP observe streams."""

import asyncio

import pytest

from coap_client_for_esphome.coordinator import CoapCoordinator


async def _start_and_settle(coordinator, delay: float = 0.2) -> None:
    await coordinator.async_setup()
    coordinator.async_start_observations()
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Periodic resubscription
# ---------------------------------------------------------------------------


async def test_periodic_resubscription_fires(hass, mock_server):
    """observe_register_count increases after the resubscription interval elapses."""
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        resubscribe_interval_s=0.3,
    )
    try:
        await _start_and_settle(coord, delay=0.2)
        count_before = mock_server._entities["temperature"].observe_register_count
        assert count_before >= 1
        await asyncio.sleep(0.5)  # well past the 0.3 s interval
        assert mock_server._entities["temperature"].observe_register_count > count_before
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_periodic_resubscription_does_not_consume_retry_budget(hass, mock_server):
    """Planned resubscriptions must not count against observe_retry."""
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        resubscribe_interval_s=0.2,
    )
    try:
        await _start_and_settle(coord, delay=0.1)
        # Let 4+ resubscription cycles fire; observe_retry default is 0 so any
        # retry-budget consumption would immediately mark unavailable.
        await asyncio.sleep(1.0)
        assert coord.available is True
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_notifications_delivered_after_periodic_resubscription(hass, mock_server):
    """Notifications are delivered normally after at least one resubscription."""
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        resubscribe_interval_s=0.3,
    )
    try:
        await _start_and_settle(coord, delay=0.5)  # at least one resubscription
        mock_server.set_value("temperature", 99.0)
        await asyncio.sleep(0.2)
        assert coord.get_state("temperature")["value"] == 99.0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_resubscription_delivers_fresh_state(hass, mock_server):
    """The GET issued on resubscription delivers the current server state."""
    coord = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        resubscribe_interval_s=0.3,
    )
    try:
        await _start_and_settle(coord, delay=0.1)
        # Change value right before resubscription fires; the GET response should
        # deliver the new value even if no notification was sent in between.
        mock_server.set_value("temperature", 55.0)
        await asyncio.sleep(0.5)  # past the 0.3 s interval
        assert coord.get_state("temperature")["value"] == pytest.approx(55.0)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Manual resubscription (async_resubscribe)
# ---------------------------------------------------------------------------


async def test_manual_resubscribe_increments_register_count(hass, mock_server):
    """async_resubscribe() sends a fresh GET+Observe=0 for all resources."""
    coord = CoapCoordinator(hass=hass, host=mock_server.host, port=mock_server.port)
    try:
        await _start_and_settle(coord)
        count_before = mock_server._entities["temperature"].observe_register_count
        assert count_before >= 1

        coord.async_resubscribe()
        await asyncio.sleep(0.2)

        assert mock_server._entities["temperature"].observe_register_count > count_before
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_manual_resubscribe_notifications_still_work(hass, mock_server):
    """Notifications are delivered normally after manual resubscription."""
    coord = CoapCoordinator(hass=hass, host=mock_server.host, port=mock_server.port)
    try:
        await _start_and_settle(coord)

        coord.async_resubscribe()
        await asyncio.sleep(0.2)

        mock_server.set_value("temperature", 77.0)
        await asyncio.sleep(0.2)
        assert coord.get_state("temperature")["value"] == pytest.approx(77.0)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_manual_resubscribe_coordinator_stays_available(hass, mock_server):
    """async_resubscribe() does not mark the coordinator unavailable."""
    coord = CoapCoordinator(hass=hass, host=mock_server.host, port=mock_server.port)
    try:
        await _start_and_settle(coord)
        assert coord.available is True

        coord.async_resubscribe()
        await asyncio.sleep(0.2)

        assert coord.available is True
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_manual_resubscribe_replaces_observe_tasks(hass, mock_server):
    """async_resubscribe() cancels old tasks and creates fresh ones."""
    coord = CoapCoordinator(hass=hass, host=mock_server.host, port=mock_server.port)
    try:
        await _start_and_settle(coord)
        tasks_before = list(coord._observe_tasks)

        coord.async_resubscribe()
        await asyncio.sleep(0.1)

        # All old tasks should be cancelled or done
        for t in tasks_before:
            assert t.done()
        # New tasks should exist
        assert len(coord._observe_tasks) > 0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
