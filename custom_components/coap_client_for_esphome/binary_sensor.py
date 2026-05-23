"""Binary sensor platform for the CoAP Client integration."""

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .coordinator import CoapCoordinator
from .entity import CoapEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP binary sensor entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapBinarySensor(coordinator, resource, entry)
        for resource in coordinator.binary_sensors
    )


class CoapBinarySensor(CoapEntity, BinarySensorEntity):
    """A binary sensor entity backed by a CoAP observable resource."""

    async def async_added_to_hass(self) -> None:
        """Register state subscription."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.subscribe(self._resource.name, self._handle_update)
        )
        if data := self._coordinator.get_state(self._resource.name):
            self._attr_is_on = bool(data.get("value"))

    @callback
    def _handle_update(self, data: dict[str, Any]) -> None:
        self._attr_is_on = bool(data.get("value"))
        self.async_write_ha_state()
