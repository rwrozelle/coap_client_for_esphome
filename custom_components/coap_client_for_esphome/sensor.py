"""Sensor platform for the CoAP Client integration."""

import contextlib
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .coordinator import CoapCoordinator, CoapResource
from .entity import CoapEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP sensor and text sensor entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    entities: list[CoapEntity] = [
        CoapSensor(coordinator, resource, entry) for resource in coordinator.sensors
    ]
    entities += [
        CoapTextSensor(coordinator, resource, entry)
        for resource in coordinator.text_sensors
    ]
    async_add_entities(entities)


class CoapSensor(CoapEntity, SensorEntity):
    """A sensor entity backed by a CoAP observable resource."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: CoapCoordinator,
        resource: CoapResource,
        entry: CoapClientConfigEntry,
    ) -> None:
        """Initialize the sensor entity."""
        super().__init__(coordinator, resource, entry)
        self._attr_native_unit_of_measurement = resource.unit or None
        if resource.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = SensorDeviceClass(resource.device_class)
        if resource.accuracy_decimals is not None:
            self._attr_suggested_display_precision = resource.accuracy_decimals

    async def async_added_to_hass(self) -> None:
        """Register state subscription."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.subscribe(self._resource.name, self._handle_update)
        )
        if data := self._coordinator.get_state(self._resource.name):
            self._apply(data)

    @callback
    def _handle_update(self, data: dict[str, Any]) -> None:
        self._apply(data)
        self.async_write_ha_state()

    def _apply(self, data: dict[str, Any]) -> None:
        self._attr_native_value = data.get("value")
        if "unit" in data and not self._attr_native_unit_of_measurement:
            self._attr_native_unit_of_measurement = data["unit"]


class CoapTextSensor(CoapEntity, SensorEntity):
    """A text sensor entity backed by a CoAP observable resource."""

    def __init__(
        self,
        coordinator: CoapCoordinator,
        resource: CoapResource,
        entry: CoapClientConfigEntry,
    ) -> None:
        """Initialize the text sensor entity."""
        super().__init__(coordinator, resource, entry)
        if resource.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = SensorDeviceClass(resource.device_class)

    async def async_added_to_hass(self) -> None:
        """Register state subscription."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.subscribe(self._resource.name, self._handle_update)
        )
        if data := self._coordinator.get_state(self._resource.name):
            self._attr_native_value = data.get("value")

    @callback
    def _handle_update(self, data: dict[str, Any]) -> None:
        self._attr_native_value = data.get("value")
        self.async_write_ha_state()
