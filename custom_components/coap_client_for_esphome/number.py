"""Number platform for the CoAP Client integration."""

import contextlib
from typing import Any

import cbor2

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .const import SENML_V
from .coordinator import CoapCoordinator, CoapResource
from .entity import CoapEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP number entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapNumber(coordinator, resource, entry) for resource in coordinator.numbers
    )


class CoapNumber(CoapEntity, NumberEntity):
    """A number entity backed by a CoAP observable resource with GET/POST."""

    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_step = 1.0
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        coordinator: CoapCoordinator,
        resource: CoapResource,
        entry: CoapClientConfigEntry,
    ) -> None:
        """Initialize the number entity."""
        super().__init__(coordinator, resource, entry)
        if resource.device_class:
            with contextlib.suppress(ValueError):
                self._attr_device_class = NumberDeviceClass(resource.device_class)

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
        self._attr_assumed_state = False
        self.async_write_ha_state()

    async def async_set_native_value(self, value: float) -> None:
        """Set the number value."""
        self._attr_native_value = value
        self._attr_assumed_state = True
        self.async_write_ha_state()
        resource = self._coordinator.get_resource_by_name(self._resource.name)
        if resource is None:
            return
        payload = cbor2.dumps({SENML_V: value})
        self.hass.async_create_background_task(
            self._post_and_confirm(resource.path, payload),
            name=f"coap_post_{resource.path}",
        )

    async def _post_and_confirm(self, path: str, payload: bytes) -> None:
        try:
            data = await self._coordinator.async_post(path, payload)
            if data is not None:
                self._attr_native_value = data.get("value")
                self._attr_assumed_state = False
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001
            pass
