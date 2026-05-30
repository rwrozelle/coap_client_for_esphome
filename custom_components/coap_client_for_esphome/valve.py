"""Valve platform for the CoAP Client integration."""

import contextlib
import logging
from typing import Any

import cbor2

from homeassistant.components.valve import (
    ValveDeviceClass,
    ValveEntity,
    ValveEntityFeature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .const import SENML_VB
from .coordinator import CoapCoordinator, CoapResource
from .entity import CoapEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP valve entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapValve(coordinator, resource, entry) for resource in coordinator.valves
    )


class CoapValve(CoapEntity, ValveEntity):
    """A valve entity backed by a CoAP observable resource with action endpoints."""

    _attr_reports_position = True
    _attr_supported_features = (
        ValveEntityFeature.OPEN | ValveEntityFeature.CLOSE | ValveEntityFeature.STOP
    )

    def __init__(
        self,
        coordinator: CoapCoordinator,
        resource: CoapResource,
        entry: CoapClientConfigEntry,
    ) -> None:
        """Initialize the valve entity."""
        super().__init__(coordinator, resource, entry)
        if resource.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = ValveDeviceClass(resource.device_class)

    async def async_added_to_hass(self) -> None:
        """Register state subscription."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.subscribe(self._resource.name, self._handle_update)
        )
        if data := self._coordinator.get_state(self._resource.name):
            self._apply_state(data)

    @callback
    def _handle_update(self, data: dict[str, Any]) -> None:
        self._apply_state(data)
        self.async_write_ha_state()

    def _apply_state(self, data: dict[str, Any]) -> None:
        raw = data.get("value")
        if raw is not None:
            try:
                self._attr_current_valve_position = max(0, min(100, round(float(raw) * 100)))
            except (TypeError, ValueError):
                _LOGGER.warning("Invalid valve position value: %r", raw)
                return
        self._attr_is_opening = False
        self._attr_is_closing = False
        self._attr_assumed_state = False

    async def async_open_valve(self) -> None:
        """Open the valve."""
        self._attr_is_opening = True
        self._attr_is_closing = False
        self._attr_assumed_state = True
        self.async_write_ha_state()
        path = self._resource.path
        self.hass.async_create_background_task(
            self._post_and_confirm(path, True),
            name=f"coap_post_{path}",
        )

    async def async_close_valve(self) -> None:
        """Close the valve."""
        self._attr_is_closing = True
        self._attr_is_opening = False
        self._attr_assumed_state = True
        self.async_write_ha_state()
        path = self._resource.path
        self.hass.async_create_background_task(
            self._post_and_confirm(path, False),
            name=f"coap_post_{path}",
        )

    async def async_stop_valve(self) -> None:
        """Stop the valve."""
        self._attr_assumed_state = True
        self.async_write_ha_state()
        stop_path = self._resource.stop_path or self._resource.path[:-1] + "2"
        self.hass.async_create_background_task(
            self._post_and_confirm(stop_path, True),
            name=f"coap_post_{stop_path}",
        )

    async def _post_and_confirm(self, path: str, value: bool) -> None:
        try:
            payload = cbor2.dumps({SENML_VB: value})
            data = await self._coordinator.async_post(path, payload)
            if data is not None:
                self._apply_state(data)
                self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("POST to %s failed: %s", path, err)
            if data := self._coordinator.get_state(self._resource.name):
                self._apply_state(data)
                self.async_write_ha_state()
