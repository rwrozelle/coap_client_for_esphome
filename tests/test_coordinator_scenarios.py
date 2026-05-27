"""Scenario-based integration tests for coordinator.py.

Each test represents a real-world operational scenario (reboot, reflash,
config change, etc.) and asserts on the coordinator's observable behaviour.
"""

import asyncio

import pytest

from coap_client_for_esphome.const import (
    CONF_ID_CONTEXT,
    CONF_MASTER_SALT,
    CONF_MASTER_SECRET,
    CONF_OSCORE_SEQ_THRESHOLD,
    CONF_RECIPIENT_ID,
    CONF_SENDER_ID,
)
from coap_client_for_esphome.coordinator import CoapCoordinator
from tests.conftest import FULL_LINK_FORMAT, REDUCED_LINK_FORMAT


# ---------------------------------------------------------------------------
# Scenario 1: server gains OSCORE after reboot — client has no OSCORE config
# ---------------------------------------------------------------------------


async def test_plaintext_client_vs_oscore_required_server_stays_unavailable(
    hass, oscore_required_server
):
    """Client with no OSCORE config cannot subscribe to OSCORE-required resources.

    Server was reconfigured to require OSCORE. Client still sends plaintext
    observe requests. Server responds 4.01 Unauthorized. Coordinator must
    NOT flip to available.
    """
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_required_server.host,
        port=oscore_required_server.port,
    )
    try:
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.3)
        assert coord.available is False
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_plaintext_client_vs_oscore_required_server_no_state_delivered(
    hass, oscore_required_server
):
    """No entity state is delivered when server requires OSCORE and client has none."""
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_required_server.host,
        port=oscore_required_server.port,
    )
    try:
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.3)
        assert coord.get_state("temperature") is None
        assert coord.get_state("motion") is None
        assert coord.get_state("relay") is None
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Scenario 2: server reflashed with fewer components
# ---------------------------------------------------------------------------


async def test_resource_set_reduction_triggers_reload(hass):
    """Reconnect after reflash with fewer entities triggers an integration reload.

    Server starts with the full entity set (sensor, switch, button, binary_sensor,
    text_sensor, number, lock, valve). After reflash it serves only a sensor.
    On reconnect the coordinator detects the name set changed and calls
    async_schedule_reload instead of restarting observations.
    """
    from tests.conftest import MockCoapServer

    server = MockCoapServer(link_format=FULL_LINK_FORMAT)
    await server.start()
    try:
        coord = CoapCoordinator(
            hass=hass,
            host=server.host,
            port=server.port,
            entry_id="test_entry",
        )
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        # Simulate reflash: server now advertises only the uptime sensor
        server.set_link_format(REDUCED_LINK_FORMAT)

        await coord._async_reconnect()

        assert hass.config_entries.reload_calls == ["test_entry"]
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


async def test_resource_set_reduction_does_not_restart_observations(hass):
    """When resource set shrinks, observations are not restarted (reload handles it)."""
    from tests.conftest import MockCoapServer

    server = MockCoapServer(link_format=FULL_LINK_FORMAT)
    await server.start()
    try:
        coord = CoapCoordinator(
            hass=hass,
            host=server.host,
            port=server.port,
            entry_id="test_entry",
        )
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        tasks_before = len(coord._observe_tasks)
        server.set_link_format(REDUCED_LINK_FORMAT)
        await coord._async_reconnect()

        # Reconnect path returned early (reload scheduled) — no new tasks added
        assert len(coord._observe_tasks) == tasks_before
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


# ---------------------------------------------------------------------------
# Scenario 3: OSCORE reconfigure — client gains credentials, then connects
# ---------------------------------------------------------------------------


async def test_reconfigure_adds_oscore_makes_client_available(
    hass, oscore_server_with_decrypt
):
    """Adding OSCORE credentials via reconfigure makes the coordinator available.

    Phase 1: coordinator has no OSCORE config. Server requires OSCORE and
    performs real decryption. Client sends plaintext observe → server
    returns 4.01 Unauthorized → coordinator stays unavailable.

    Phase 2: OSCORE credentials are added (simulating HA reconfigure flow).
    Observations are cancelled and restarted. Coordinator now sends
    OSCORE-protected requests → server decrypts, responds → coordinator
    marks itself available and delivers entity state.
    """
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
    )
    try:
        # Phase 1: no OSCORE → stays unavailable
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)
        assert coord.available is False

        # Phase 2: add OSCORE credentials and restart
        coord._cancel_observations()
        coord._oscore_config = {
            CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
            CONF_MASTER_SALT: "9e7ca92223786340",
            CONF_SENDER_ID: "02",
            CONF_RECIPIENT_ID: "01",
            CONF_ID_CONTEXT: "",
            CONF_OSCORE_SEQ_THRESHOLD: "0",
        }
        coord._configure_oscore()
        coord.async_start_observations()
        await asyncio.sleep(0.5)
        assert coord.available is True
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_reconfigure_adds_oscore_delivers_state(hass, oscore_server_with_decrypt):
    """After OSCORE reconfiguration, entity state is delivered from the server."""
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
    )
    try:
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        # Reconfigure with OSCORE
        coord._cancel_observations()
        coord._oscore_config = {
            CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
            CONF_MASTER_SALT: "9e7ca92223786340",
            CONF_SENDER_ID: "02",
            CONF_RECIPIENT_ID: "01",
            CONF_ID_CONTEXT: "",
            CONF_OSCORE_SEQ_THRESHOLD: "0",
        }
        coord._configure_oscore()
        coord.async_start_observations()
        await asyncio.sleep(0.5)

        assert coord.get_state("temperature") is not None
        assert coord.get_state("temperature")["value"] == 20.0
        assert coord.get_state("motion") is not None
        assert coord.get_state("relay") is not None
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Scenario 4: OSCORE reconfigure with wrong credentials — connected client loses link
# ---------------------------------------------------------------------------

_GOOD_OSCORE_CFG = {
    CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
    CONF_MASTER_SALT: "9e7ca92223786340",
    CONF_SENDER_ID: "02",
    CONF_RECIPIENT_ID: "01",
    CONF_ID_CONTEXT: "",
    CONF_OSCORE_SEQ_THRESHOLD: "0",
}

_BAD_OSCORE_CFG = {
    CONF_MASTER_SECRET: "aabbccddaabbccddaabbccddaabbccdd",  # wrong key
    CONF_MASTER_SALT: "9e7ca92223786340",
    CONF_SENDER_ID: "02",
    CONF_RECIPIENT_ID: "01",
    CONF_ID_CONTEXT: "",
    CONF_OSCORE_SEQ_THRESHOLD: "0",
}


async def test_reconfigure_wrong_oscore_makes_client_unavailable(
    hass, oscore_server_with_decrypt
):
    """Changing OSCORE credentials to wrong values makes the coordinator unavailable.

    Phase 1: coordinator connects with correct OSCORE credentials → available.
    Phase 2: user reconfigures with wrong master_secret. Outgoing OSCORE
    messages are encrypted with wrong keys. Server AEAD decryption fails →
    server returns plaintext 4.01. aiocoap raises NotAProtectedMessage
    (the response has no OSCORE option). Coordinator catches the error and
    calls _set_available(False).
    """
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
        oscore_config=_GOOD_OSCORE_CFG,
    )
    try:
        # Phase 1: correct OSCORE → available
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.5)
        assert coord.available is True

        # Phase 2: wrong OSCORE → unavailable
        coord._cancel_observations()
        coord._oscore_config = _BAD_OSCORE_CFG
        coord._configure_oscore()
        coord.async_start_observations()
        await asyncio.sleep(0.3)
        assert coord.available is False
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_reconfigure_wrong_oscore_no_state_updates(
    hass, oscore_server_with_decrypt
):
    """No new state is delivered after OSCORE credentials are changed to wrong values.

    The server changes the temperature value between phase 1 and phase 2.
    After the bad reconfigure the observation fails, so the new server value
    never reaches the coordinator — the state subscriber sees no new call.
    """
    coord = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
        oscore_config=_GOOD_OSCORE_CFG,
    )
    try:
        # Phase 1: connect and capture initial state
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.5)
        assert coord.available is True
        state_after_phase1 = coord.get_state("temperature")
        assert state_after_phase1 is not None

        updates: list = []
        coord.subscribe("temperature", lambda s: updates.append(s))

        # Change the server value — should NOT reach coordinator after bad reconfigure
        oscore_server_with_decrypt.set_value("temperature", 99.0)

        # Phase 2: bad reconfigure — observations fail
        coord._cancel_observations()
        coord._oscore_config = _BAD_OSCORE_CFG
        coord._configure_oscore()
        coord.async_start_observations()
        await asyncio.sleep(0.3)

        assert coord.available is False
        assert updates == []  # no callbacks fired after the bad reconfigure
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Scenario 5: user enables subscribe_logs
# ---------------------------------------------------------------------------


async def test_subscribe_logs_notification_reaches_ha_logger(hass, mock_server, caplog):
    """Log notifications from the server appear in the HA logger.

    When subscribe_logs=True and the server sends a log notification in
    ESPHome log format ([[millis, level, tag, message], ...]), the coordinator
    decodes it and emits each entry via _LOGGER.log(). The message and tag must
    appear in the captured log output.
    """
    import logging

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

        with caplog.at_level(logging.INFO, logger="coap_client_for_esphome.coordinator"):
            mock_server.trigger_log_notification([[1000, 3, "sensor", "Temperature read"]])
            await asyncio.sleep(0.2)

        assert any("Temperature read" in r.message for r in caplog.records)
        assert any("[sensor]" in r.message for r in caplog.records)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_subscribe_logs_level_mapping(hass, mock_server, caplog):
    """ESPHome log levels (1–5) are mapped to the correct Python log levels.

    ESPHome level 1 → ERROR, 2 → WARNING, 3 → INFO, 4 → DEBUG, 5 → DEBUG.
    The coordinator uses _ESPHOME_TO_PY_LEVEL to translate before calling
    _LOGGER.log(), so each level must appear at the right severity in caplog.
    """
    import logging

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

        with caplog.at_level(logging.DEBUG, logger="coap_client_for_esphome.coordinator"):
            mock_server.trigger_log_notification([
                [0, 1, "t", "error-msg"],
                [0, 2, "t", "warning-msg"],
                [0, 3, "t", "info-msg"],
                [0, 4, "t", "debug-msg"],
                [0, 5, "t", "verbose-msg"],
            ])
            await asyncio.sleep(0.2)

        by_msg = {r.message: r.levelno for r in caplog.records}
        assert any("error-msg" in m and lvl == logging.ERROR for m, lvl in by_msg.items())
        assert any("warning-msg" in m and lvl == logging.WARNING for m, lvl in by_msg.items())
        assert any("info-msg" in m and lvl == logging.INFO for m, lvl in by_msg.items())
        assert any("debug-msg" in m and lvl == logging.DEBUG for m, lvl in by_msg.items())
        assert any("verbose-msg" in m and lvl == logging.DEBUG for m, lvl in by_msg.items())
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_subscribe_logs_malformed_entries_do_not_crash(hass, mock_server):
    """Malformed log payloads are silently ignored — coordinator stays available.

    Entries that are not 4-element lists are skipped. An empty list, a dict,
    or a short list must all be tolerated without raising.
    """
    import cbor2

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
        assert coord.available is True

        # Various malformed payloads — all must be silently dropped
        mock_server.trigger_log_notification([])                    # empty list
        await asyncio.sleep(0.1)
        mock_server.trigger_log_notification([[1, 2, 3]])           # entry too short
        await asyncio.sleep(0.1)
        mock_server.trigger_log_notification([[1, 2, 3, 4, 5]])     # entry too long
        await asyncio.sleep(0.1)
        mock_server.trigger_log_notification([{"bad": "dict"}])     # entry is a dict
        await asyncio.sleep(0.1)

        assert coord.available is True
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_subscribe_logs_notification_records_pong(hass, mock_server):
    """Each log notification updates the last-server-pong timestamp.

    _async_observe_logs() calls record_server_pong() for every observation,
    which resets the idle timer that drives the ping/reconnect logic.
    """
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

        pong_before = coord._last_server_pong
        await asyncio.sleep(0.05)

        mock_server.trigger_log_notification([[1000, 3, "app", "ping-check"]])
        await asyncio.sleep(0.2)

        assert coord._last_server_pong > pong_before
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


# ---------------------------------------------------------------------------
# Scenario 6: user deselects subscribe_logs — opposite of Scenario 5
# ---------------------------------------------------------------------------


async def test_unsubscribe_logs_no_log_task_after_restart(hass, mock_server):
    """After deselecting subscribe_logs, no log observation task is running.

    Phase 1: subscribe_logs=True — log task is active.
    Phase 2: subscribe_logs toggled off, observations restarted — log task gone.
    """
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
        assert any("log" in t.get_name() for t in coord._observe_tasks)

        coord._cancel_observations()
        coord._subscribe_logs = False
        coord.async_start_observations()
        await asyncio.sleep(0.1)

        assert not any("log" in t.get_name() for t in coord._observe_tasks)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_unsubscribe_logs_notifications_no_longer_forwarded(
    hass, mock_server, caplog
):
    """Log notifications from the server are silently dropped after deselecting.

    Phase 1: subscribe_logs=True — notifications reach the HA logger.
    Phase 2: subscribe_logs toggled off — new notifications produce no log records.
    """
    import logging

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

        # Phase 1: confirm forwarding is working
        with caplog.at_level(logging.INFO, logger="coap_client_for_esphome.coordinator"):
            mock_server.trigger_log_notification([[0, 3, "t", "phase1-marker"]])
            await asyncio.sleep(0.2)
        assert any("phase1-marker" in r.message for r in caplog.records)

        # Phase 2: deselect logs, restart
        coord._cancel_observations()
        coord._subscribe_logs = False
        coord.async_start_observations()
        await asyncio.sleep(0.1)

        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger="coap_client_for_esphome.coordinator"):
            mock_server.trigger_log_notification([[0, 3, "t", "phase2-marker"]])
            await asyncio.sleep(0.2)

        assert not any("phase2-marker" in r.message for r in caplog.records)
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_unsubscribe_logs_sends_observe1_to_server(hass, mock_server):
    """Coordinator sends GET Observe=1 to the server when the log observation is cancelled.

    The ESPHome C++ coap_server deregisters observers on GET Observe=1 (not RST).
    When _cancel_observations() is called, the log task's finally block must
    dispatch a NON GET Observe=1 to the server so it removes the subscriber entry.
    The mock server's _LogResource.render_get() records the deregister request.
    """
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
        assert len(mock_server._log._observations) > 0

        coord._cancel_observations()
        await asyncio.sleep(0.2)  # let the deregister request be dispatched and processed

        assert mock_server._log.deregister_received is True
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_unsubscribe_logs_entity_observations_unaffected(hass, mock_server):
    """Entity observations and availability are unchanged after deselecting logs.

    After removing the log subscription, the coordinator must remain available
    and continue delivering entity state updates.
    """
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
        assert coord.available is True

        coord._cancel_observations()
        coord._subscribe_logs = False
        coord.async_start_observations()
        await asyncio.sleep(0.2)

        assert coord.available is True

        mock_server.set_value("temperature", 55.0)
        await asyncio.sleep(0.2)
        assert coord.get_state("temperature")["value"] == 55.0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()


async def test_unchanged_resource_set_does_not_trigger_reload(hass):
    """Reconnect with same entity set restarts observations without a reload."""
    from tests.conftest import MockCoapServer

    server = MockCoapServer(link_format=FULL_LINK_FORMAT)
    await server.start()
    try:
        coord = CoapCoordinator(
            hass=hass,
            host=server.host,
            port=server.port,
            entry_id="test_entry",
        )
        await coord.async_setup()
        coord.async_start_observations()
        await asyncio.sleep(0.2)
        coord._cancel_observations()

        # Link format unchanged — same entity names
        await coord._async_reconnect()

        assert hass.config_entries.reload_calls == []
        assert len(coord._observe_tasks) > 0
    finally:
        await coord.async_teardown()
        await hass.cancel_all_tasks()
        await server.stop()


# ---------------------------------------------------------------------------
# Scenario 7: User selects Reload
#
# HA unloads the config entry (async_teardown) then sets it up fresh
# (new CoapCoordinator, async_setup, async_start_observations). The
# coordinator must recover fully — available, state delivered.
#
# Rename / Disable / Enable / Delete and all sub-device operations (disable,
# enable, move area) are handled entirely by HA's entity/device registries and
# do not interact with the coordinator. No coordinator tests are needed for
# those actions.
# ---------------------------------------------------------------------------


async def test_reload_restores_availability(hass, mock_server):
    """After reload, a fresh coordinator becomes available on the same server.

    Simulates HA unloading then re-loading the config entry: old coordinator
    torn down, new one created with the same host/port, setup, and observations
    started. The new coordinator must reach available state.
    """
    coord1 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
    )
    await coord1.async_setup()
    coord1.async_start_observations()
    await asyncio.sleep(0.2)
    assert coord1.available is True
    await coord1.async_teardown()

    coord2 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
    )
    try:
        await coord2.async_setup()
        coord2.async_start_observations()
        await asyncio.sleep(0.2)
        assert coord2.available is True
    finally:
        await coord2.async_teardown()
        await hass.cancel_all_tasks()


async def test_reload_delivers_current_state(hass, mock_server):
    """After reload, the fresh coordinator delivers the server's current state.

    The server value is changed while the first coordinator is torn down.
    The new coordinator must observe and deliver the updated value.
    """
    coord1 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
    )
    await coord1.async_setup()
    coord1.async_start_observations()
    await asyncio.sleep(0.2)
    await coord1.async_teardown()

    mock_server.set_value("temperature", 77.0)

    coord2 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
    )
    try:
        await coord2.async_setup()
        coord2.async_start_observations()
        await asyncio.sleep(0.2)
        assert coord2.get_state("temperature")["value"] == 77.0
    finally:
        await coord2.async_teardown()
        await hass.cancel_all_tasks()


async def test_reload_with_oscore_restores_availability(hass, oscore_server_with_decrypt):
    """After reload, a fresh OSCORE coordinator is available.

    Simulates HA saving the OSCORE sequence number threshold and passing it
    back to the new coordinator on re-setup (as happens via entry.data). The
    new coordinator starts its sender sequence number at the saved threshold
    and must successfully authenticate with the server.
    """
    coord1 = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
        oscore_config=_GOOD_OSCORE_CFG,
    )
    await coord1.async_setup()
    coord1.async_start_observations()
    await asyncio.sleep(0.5)
    assert coord1.available is True
    saved_threshold = coord1._oscore_ctx._oscore_seq_threshold
    await coord1.async_teardown()

    # HA restores the saved threshold in the new coordinator's oscore_config
    reloaded_cfg = {**_GOOD_OSCORE_CFG, CONF_OSCORE_SEQ_THRESHOLD: str(saved_threshold)}
    coord2 = CoapCoordinator(
        hass=hass,
        host=oscore_server_with_decrypt.host,
        port=oscore_server_with_decrypt.port,
        oscore_config=reloaded_cfg,
    )
    try:
        await coord2.async_setup()
        coord2.async_start_observations()
        await asyncio.sleep(0.5)
        assert coord2.available is True
        assert coord2._oscore_ctx.sender_sequence_number >= saved_threshold
    finally:
        await coord2.async_teardown()
        await hass.cancel_all_tasks()


async def test_reload_with_subscribe_logs_restores_log_task(hass, mock_server, caplog):
    """After reload with subscribe_logs=True, the log observation task is running.

    When HA reloads an entry that had subscribe_logs enabled in its options,
    the new coordinator is created with subscribe_logs=True. The log task must
    be active and able to receive notifications after re-setup.
    """
    import logging

    coord1 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        subscribe_logs=True,
    )
    await coord1.async_setup()
    coord1.async_start_observations()
    await asyncio.sleep(0.2)
    await coord1.async_teardown()
    mock_server.clear_log_observers()  # aiocoap doesn't send deregister on teardown

    coord2 = CoapCoordinator(
        hass=hass,
        host=mock_server.host,
        port=mock_server.port,
        subscribe_logs=True,
    )
    try:
        await coord2.async_setup()
        coord2.async_start_observations()
        await asyncio.sleep(0.2)

        assert any("log" in t.get_name() for t in coord2._observe_tasks)

        with caplog.at_level(logging.INFO, logger="coap_client_for_esphome.coordinator"):
            mock_server.trigger_log_notification([[0, 3, "app", "reload-check"]])
            await asyncio.sleep(0.2)
        assert any("reload-check" in r.message for r in caplog.records)
    finally:
        await coord2.async_teardown()
        await hass.cancel_all_tasks()
