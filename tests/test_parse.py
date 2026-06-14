"""Unit tests for _parse_link_format and _parse_cbor_state — no network needed."""

import cbor2
import pytest

from coap_client_for_esphome.coordinator import _parse_cbor_state, _parse_link_format
from coap_client_for_esphome.const import SENML_U, SENML_V, SENML_VB, SENML_VS


# ---------------------------------------------------------------------------
# _parse_link_format
# ---------------------------------------------------------------------------


def test_parse_link_format_single_sensor():
    text = '</fp/1>;rt="esphome.sensor";obs;oid="temperature";uom="°C";dc="temperature"'
    resources = _parse_link_format(text)
    assert len(resources) == 1
    r = resources[0]
    assert r.path == "fp/1"
    assert r.resource_type == "esphome.sensor"
    assert r.observable is True
    assert r.name == "temperature"
    assert r.unit == "°C"
    assert r.device_class == "temperature"


def test_parse_link_format_multiple_resources():
    text = (
        '</fp/1>;rt="esphome.sensor";obs;oid="temp",'
        '</fp/2>;rt="esphome.binary_sensor";obs;oid="motion",'
        '</ping>;rt="esphome.ping"'
    )
    resources = _parse_link_format(text)
    assert len(resources) == 3
    paths = [r.path for r in resources]
    assert "fp/1" in paths
    assert "fp/2" in paths
    assert "ping" in paths


def test_parse_link_format_observable_flag():
    obs_text = '</fp/1>;obs'
    non_obs_text = '</fp/2>'
    obs = _parse_link_format(obs_text)[0]
    non_obs = _parse_link_format(non_obs_text)[0]
    assert obs.observable is True
    assert non_obs.observable is False


def test_parse_link_format_content_type():
    text = '</fp/1>;ct=60'
    r = _parse_link_format(text)[0]
    assert r.content_type == 60


def test_parse_link_format_title_fallback_for_name():
    text = '</fp/1>;title="My Sensor"'
    r = _parse_link_format(text)[0]
    assert r.name == "My Sensor"


def test_parse_link_format_oid_takes_priority_over_title():
    text = '</fp/1>;title="Fallback";oid="primary"'
    r = _parse_link_format(text)[0]
    assert r.name == "primary"


def test_parse_link_format_device_index():
    text = '</fp/1>;oid="sensor";dv=2'
    r = _parse_link_format(text)[0]
    assert r.device_index == 2


def test_parse_link_format_stop_path():
    text = '</fp/3>;oid="valve";stp=/fp/3/stop'
    r = _parse_link_format(text)[0]
    assert r.stop_path == "fp/3/stop"


def test_parse_link_format_leading_slash_stripped_from_path():
    text = '</fp/1>;oid="x"'
    r = _parse_link_format(text)[0]
    assert not r.path.startswith("/")


def test_parse_link_format_skips_malformed_entries():
    text = 'not-a-link,</fp/1>;oid="ok"'
    resources = _parse_link_format(text)
    assert len(resources) == 1
    assert resources[0].name == "ok"


def test_parse_link_format_empty_string():
    assert _parse_link_format("") == []


# ---------------------------------------------------------------------------
# _parse_cbor_state
# ---------------------------------------------------------------------------


def test_parse_cbor_state_float_value():
    payload = cbor2.dumps([{SENML_V: 25.5}])
    result = _parse_cbor_state(payload)
    assert result == {"value": 25.5}


def test_parse_cbor_state_boolean_true():
    payload = cbor2.dumps([{SENML_VB: True}])
    result = _parse_cbor_state(payload)
    assert result == {"value": True}


def test_parse_cbor_state_boolean_false():
    payload = cbor2.dumps([{SENML_VB: False}])
    result = _parse_cbor_state(payload)
    assert result == {"value": False}


def test_parse_cbor_state_string_value():
    payload = cbor2.dumps([{SENML_VS: "locked"}])
    result = _parse_cbor_state(payload)
    assert result == {"value": "locked"}


def test_parse_cbor_state_na_string_becomes_none():
    payload = cbor2.dumps([{SENML_VS: "NA"}])
    result = _parse_cbor_state(payload)
    assert result == {"value": None}


def test_parse_cbor_state_includes_unit():
    payload = cbor2.dumps([{SENML_V: 22.0, SENML_U: "°C"}])
    result = _parse_cbor_state(payload)
    assert result["value"] == 22.0
    assert result["unit"] == "°C"


def test_parse_cbor_state_dict_instead_of_list():
    # Server may send a bare dict instead of a single-element list
    payload = cbor2.dumps({SENML_V: 10.0})
    result = _parse_cbor_state(payload)
    assert result == {"value": 10.0}


def test_parse_cbor_state_empty_payload():
    assert _parse_cbor_state(b"") is None


def test_parse_cbor_state_no_value_key_returns_none():
    # Dict with no SENML value key
    payload = cbor2.dumps([{SENML_U: "°C"}])
    assert _parse_cbor_state(payload) is None


def test_parse_cbor_state_invalid_cbor_returns_none():
    assert _parse_cbor_state(b"\xff\xfe\xfd") is None


def test_parse_cbor_state_non_dict_record_returns_none():
    payload = cbor2.dumps(["not", "a", "dict"])
    assert _parse_cbor_state(payload) is None


def test_parse_cbor_state_text_with_control_characters():
    """_parse_cbor_state passes text sensor strings through unchanged, including control chars."""
    value = "status\x00ok\x01\x1f"
    payload = cbor2.dumps([{SENML_VS: value}])
    result = _parse_cbor_state(payload)
    assert result is not None
    assert result["value"] == value


def test_parse_link_format_number_min_max_step():
    text = '</fp/6>;rt="esphome.number";obs;oid="brightness";min=-10;max=255;step=0.5'
    resources = _parse_link_format(text)
    assert len(resources) == 1
    r = resources[0]
    assert r.min_value == -10.0
    assert r.max_value == 255.0
    assert r.step == 0.5


def test_parse_link_format_number_no_range_attrs():
    text = '</fp/6>;rt="esphome.number";obs;oid="brightness"'
    resources = _parse_link_format(text)
    r = resources[0]
    assert r.min_value is None
    assert r.max_value is None
    assert r.step is None


def test_parse_link_format_number_partial_range():
    text = '</fp/6>;rt="esphome.number";obs;oid="brightness";step=1'
    resources = _parse_link_format(text)
    r = resources[0]
    assert r.min_value is None
    assert r.max_value is None
    assert r.step == 1.0


def test_parse_link_format_sensor_accuracy_decimals():
    text = '</fp/1>;rt="esphome.sensor";obs;oid="humidity";dc="humidity";ad=1'
    r = _parse_link_format(text)[0]
    assert r.accuracy_decimals == 1


def test_parse_link_format_sensor_accuracy_decimals_zero():
    text = '</fp/1>;rt="esphome.sensor";obs;oid="co2";ad=0'
    r = _parse_link_format(text)[0]
    assert r.accuracy_decimals == 0


def test_parse_link_format_sensor_no_accuracy_decimals():
    text = '</fp/1>;rt="esphome.sensor";obs;oid="humidity";dc="humidity"'
    r = _parse_link_format(text)[0]
    assert r.accuracy_decimals is None


def test_parse_cbor_state_integer_senml_v():
    # Lock state is encoded as CBOR uint — _parse_cbor_state coerces SENML_V to float.
    # int() still works correctly for lock state mapping (int(1.0) == 1).
    payload = cbor2.dumps([{SENML_V: 1}])
    result = _parse_cbor_state(payload)
    assert result == {"value": 1.0}
    assert int(result["value"]) == 1
