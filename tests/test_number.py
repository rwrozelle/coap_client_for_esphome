"""Unit tests for CoapNumber — min/max/step applied from resource."""

from unittest.mock import patch

from coap_client_for_esphome.coordinator import CoapResource
from coap_client_for_esphome.entity import CoapEntity
from coap_client_for_esphome.number import CoapNumber
from homeassistant.components.number import NumberMode


def _make_number(resource: CoapResource) -> CoapNumber:
    """Instantiate CoapNumber skipping the CoapEntity base init."""
    with patch.object(CoapEntity, "__init__", return_value=None):
        nb = CoapNumber(coordinator=None, resource=resource, entry=None)
    nb._resource = resource
    return nb


# ---------------------------------------------------------------------------
# min/max/step applied from resource
# ---------------------------------------------------------------------------

def test_number_uses_parsed_min_max_step():
    resource = CoapResource(
        path="fp/6/g/1", name="brightness",
        min_value=-10.0, max_value=255.0, step=0.5,
    )
    nb = _make_number(resource)
    assert nb._attr_native_min_value == -10.0
    assert nb._attr_native_max_value == 255.0
    assert nb._attr_native_step == 0.5


def test_number_falls_back_to_defaults_when_none():
    resource = CoapResource(path="fp/6/g/1", name="brightness")
    nb = _make_number(resource)
    assert nb._attr_native_min_value == 0.0
    assert nb._attr_native_max_value == 100.0
    assert nb._attr_native_step == 1.0


def test_number_mode_is_auto():
    resource = CoapResource(path="fp/6/g/1", name="brightness")
    nb = _make_number(resource)
    assert nb._attr_mode == NumberMode.AUTO


def test_number_partial_range_overrides_only_set_fields():
    resource = CoapResource(path="fp/6/g/1", name="brightness", step=0.1)
    nb = _make_number(resource)
    assert nb._attr_native_min_value == 0.0    # default
    assert nb._attr_native_max_value == 100.0  # default
    assert nb._attr_native_step == 0.1         # parsed
