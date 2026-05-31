"""Tests for config_flow helper functions and OSCORE step logic."""

# conftest.py has already registered the required HA stubs in sys.modules
# before this file is imported (pytest loads conftest first).

from unittest.mock import AsyncMock, patch

import pytest

from coap_client_for_esphome.config_flow import (
    CoapClientConfigFlow,
    _clean_hex,
    _validate_hex,
)
from coap_client_for_esphome.const import (
    CONF_ID_CONTEXT,
    CONF_MASTER_SALT,
    CONF_MASTER_SECRET,
    CONF_OSCORE,
    CONF_OSCORE_SEQ_THRESHOLD,
    CONF_RECIPIENT_ID,
    CONF_SENDER_ID,
)

# ---------------------------------------------------------------------------
# _validate_hex
# ---------------------------------------------------------------------------


def test_validate_hex_valid():
    assert _validate_hex("0102030405060708090a0b0c0d0e0f10") is True


def test_validate_hex_valid_empty():
    assert _validate_hex("") is True


def test_validate_hex_invalid_non_hex_chars():
    assert _validate_hex("xyz") is False


def test_validate_hex_invalid_odd_length():
    assert _validate_hex("abc") is False


def test_validate_hex_invalid_with_colons():
    assert _validate_hex("01:02:03") is False


# ---------------------------------------------------------------------------
# _clean_hex
# ---------------------------------------------------------------------------


def test_clean_hex_strips_leading_trailing_whitespace():
    assert _clean_hex("  0102  ") == "0102"


def test_clean_hex_removes_internal_spaces():
    assert _clean_hex("01 02 03") == "010203"


def test_clean_hex_removes_colons():
    assert _clean_hex("01:02:03") == "010203"


def test_clean_hex_lowercases():
    assert _clean_hex("AB:CD:EF") == "abcdef"


def test_clean_hex_empty():
    assert _clean_hex("") == ""


# ---------------------------------------------------------------------------
# OSCORE step validation — invalid hex input
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oscore_step_rejects_invalid_hex_in_secret():
    flow = CoapClientConfigFlow()
    flow._host = "192.168.1.1"
    flow._port = 5683
    flow._device_name = "test_device"
    flow._oscore_required = False
    result = await flow.async_step_oscore({
        CONF_MASTER_SECRET: "xyz",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "01",
        CONF_RECIPIENT_ID: "02",
        CONF_ID_CONTEXT: "",
    })
    assert result["errors"].get(CONF_MASTER_SECRET) == "invalid_hex"


@pytest.mark.asyncio
async def test_oscore_step_rejects_short_master_secret():
    flow = CoapClientConfigFlow()
    flow._host = "192.168.1.1"
    flow._port = 5683
    flow._device_name = "test_device"
    flow._oscore_required = True
    result = await flow.async_step_oscore({
        CONF_MASTER_SECRET: "0102030405060708",  # 8 hex chars = 4 bytes, too short
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "01",
        CONF_RECIPIENT_ID: "02",
        CONF_ID_CONTEXT: "",
    })
    assert result["errors"].get(CONF_MASTER_SECRET) == "oscore_secret_too_short"


@pytest.mark.asyncio
async def test_oscore_step_rejects_matching_sender_recipient():
    flow = CoapClientConfigFlow()
    flow._host = "192.168.1.1"
    flow._port = 5683
    flow._device_name = "test_device"
    flow._oscore_required = True
    result = await flow.async_step_oscore({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "01",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
    })
    assert result["errors"].get("base") == "oscore_ids_same"


@pytest.mark.asyncio
async def test_oscore_step_missing_sender_id_required():
    flow = CoapClientConfigFlow()
    flow._host = "192.168.1.1"
    flow._port = 5683
    flow._device_name = "test_device"
    flow._oscore_required = True
    result = await flow.async_step_oscore({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
    })
    assert result["errors"].get(CONF_SENDER_ID) == "oscore_field_required"


@pytest.mark.asyncio
async def test_oscore_step_valid_creates_entry():
    flow = CoapClientConfigFlow()
    flow._host = "192.168.1.1"
    flow._port = 5683
    flow._device_name = "test_device"
    flow._oscore_required = True
    result = await flow.async_step_oscore({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "9e7ca92223786340",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
    })
    assert result["type"] == "create_entry"
    oscore = result["data"][CONF_OSCORE]
    assert oscore[CONF_SENDER_ID] == "02"
    assert oscore[CONF_RECIPIENT_ID] == "01"


# ---------------------------------------------------------------------------
# Reconfigure: replay window reset
# ---------------------------------------------------------------------------


class _MockEntry:
    def __init__(self, oscore_seq: int) -> None:
        self.data = {
            "host": "192.168.1.1",
            "port": 5683,
            CONF_OSCORE: {
                CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
                CONF_MASTER_SALT: "",
                CONF_SENDER_ID: "02",
                CONF_RECIPIENT_ID: "01",
                CONF_ID_CONTEXT: "",
                CONF_OSCORE_SEQ_THRESHOLD: oscore_seq,
            },
        }


@pytest.mark.asyncio
async def test_reconfigure_reset_replay_window_zeroes_seq():
    flow = CoapClientConfigFlow()
    entry = _MockEntry(oscore_seq=2048)
    flow._get_reconfigure_entry = lambda: entry

    result = await flow.async_step_reconfigure({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        "reset_replay_window": True,
    })
    assert result["type"] == "update_and_abort"
    assert result["data"][CONF_OSCORE][CONF_OSCORE_SEQ_THRESHOLD] == 0


@pytest.mark.asyncio
async def test_reconfigure_preserves_seq_when_not_reset():
    flow = CoapClientConfigFlow()
    entry = _MockEntry(oscore_seq=2048)
    flow._get_reconfigure_entry = lambda: entry

    result = await flow.async_step_reconfigure({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        "reset_replay_window": False,
    })
    assert result["type"] == "update_and_abort"
    assert result["data"][CONF_OSCORE][CONF_OSCORE_SEQ_THRESHOLD] == 2048


@pytest.mark.asyncio
async def test_reconfigure_rejects_short_master_secret():
    flow = CoapClientConfigFlow()
    entry = _MockEntry(oscore_seq=0)
    flow._get_reconfigure_entry = lambda: entry

    result = await flow.async_step_reconfigure({
        CONF_MASTER_SECRET: "0102030405060708",  # 8 hex chars = 4 bytes, too short
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        "reset_replay_window": False,
    })
    assert result["errors"].get(CONF_MASTER_SECRET) == "oscore_secret_too_short"


@pytest.mark.asyncio
async def test_reconfigure_rejects_invalid_hex():
    flow = CoapClientConfigFlow()
    entry = _MockEntry(oscore_seq=0)
    flow._get_reconfigure_entry = lambda: entry

    result = await flow.async_step_reconfigure({
        CONF_MASTER_SECRET: "not-hex",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "02",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        "reset_replay_window": False,
    })
    assert result["errors"].get(CONF_MASTER_SECRET) == "invalid_hex"


@pytest.mark.asyncio
async def test_reconfigure_rejects_matching_ids():
    flow = CoapClientConfigFlow()
    entry = _MockEntry(oscore_seq=0)
    flow._get_reconfigure_entry = lambda: entry

    result = await flow.async_step_reconfigure({
        CONF_MASTER_SECRET: "0102030405060708090a0b0c0d0e0f10",
        CONF_MASTER_SALT: "",
        CONF_SENDER_ID: "01",
        CONF_RECIPIENT_ID: "01",
        CONF_ID_CONTEXT: "",
        "reset_replay_window": False,
    })
    assert result["errors"].get("base") == "oscore_ids_same"


# ---------------------------------------------------------------------------
# async_step_user — _fetch_device_info network path
# ---------------------------------------------------------------------------

_PATCH = "coap_client_for_esphome.config_flow._fetch_device_info"


@pytest.mark.asyncio
async def test_step_user_success_no_oscore():
    """Valid host, device reachable, no OSCORE → entry created immediately."""
    flow = CoapClientConfigFlow()
    with patch(_PATCH, new=AsyncMock(return_value=("my_device", False))):
        result = await flow.async_step_user({"host": "fd00::1", "port": 5683})
    assert result["type"] == "create_entry"
    assert result["title"] == "my_device"
    assert result["data"]["host"] == "fd00::1"
    assert result["data"]["port"] == 5683


@pytest.mark.asyncio
async def test_step_user_oscore_required_shows_oscore_form():
    """Device requires OSCORE → flow continues to OSCORE credential step."""
    flow = CoapClientConfigFlow()
    with patch(_PATCH, new=AsyncMock(return_value=("secure_device", True))):
        result = await flow.async_step_user({"host": "fd00::1", "port": 5683})
    assert result["type"] == "form"
    assert result["step_id"] == "oscore"


@pytest.mark.asyncio
async def test_step_user_cannot_connect_shows_error():
    """Network error → form re-shown with cannot_connect base error."""
    flow = CoapClientConfigFlow()
    with patch(_PATCH, side_effect=OSError("unreachable")):
        result = await flow.async_step_user({"host": "fd00::1", "port": 5683})
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_step_user_no_input_shows_blank_form():
    """No user input → blank form with no errors."""
    flow = CoapClientConfigFlow()
    result = await flow.async_step_user(None)
    assert result["type"] == "form"
    assert result["step_id"] == "user"
    assert result["errors"] == {}


# ---------------------------------------------------------------------------
# async_step_zeroconf — discovery network path
# ---------------------------------------------------------------------------


class _MockZeroconfInfo:
    """Minimal ZeroconfServiceInfo stand-in."""
    def __init__(self, ip: str, port: int = 5683) -> None:
        self.ip_address = ip
        self.port = port


@pytest.mark.asyncio
async def test_step_zeroconf_success_shows_confirm_form():
    """Discovered device, no OSCORE → zeroconf_confirm form shown."""
    flow = CoapClientConfigFlow()
    flow.context = {}
    with patch(_PATCH, new=AsyncMock(return_value=("found_device", False))):
        result = await flow.async_step_zeroconf(_MockZeroconfInfo("fd00::2"))
    assert result["type"] == "form"
    assert result["step_id"] == "zeroconf_confirm"


@pytest.mark.asyncio
async def test_step_zeroconf_confirm_creates_entry():
    """Confirming zeroconf discovery (no OSCORE) creates the config entry."""
    flow = CoapClientConfigFlow()
    flow.context = {}
    with patch(_PATCH, new=AsyncMock(return_value=("found_device", False))):
        await flow.async_step_zeroconf(_MockZeroconfInfo("fd00::2"))
    result = await flow.async_step_zeroconf_confirm({})
    assert result["type"] == "create_entry"
    assert result["title"] == "found_device"


@pytest.mark.asyncio
async def test_step_zeroconf_oscore_required_goes_to_oscore_step():
    """OSCORE-required device discovered → confirm → oscore credential form."""
    flow = CoapClientConfigFlow()
    flow.context = {}
    with patch(_PATCH, new=AsyncMock(return_value=("secure_device", True))):
        await flow.async_step_zeroconf(_MockZeroconfInfo("fd00::2"))
    result = await flow.async_step_zeroconf_confirm({})
    assert result["type"] == "form"
    assert result["step_id"] == "oscore"


@pytest.mark.asyncio
async def test_step_zeroconf_cannot_connect_aborts():
    """Network error during zeroconf discovery → flow aborted."""
    flow = CoapClientConfigFlow()
    flow.context = {}
    with patch(_PATCH, side_effect=OSError("unreachable")):
        result = await flow.async_step_zeroconf(_MockZeroconfInfo("fd00::2"))
    assert result["type"] == "abort"
    assert result["reason"] == "cannot_connect"
