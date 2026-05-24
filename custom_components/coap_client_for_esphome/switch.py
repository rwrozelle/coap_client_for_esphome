"""Switch platform for the CoAP Client integration."""

import logging
from typing import Any

import cbor2

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .const import SENML_VB
from .coordinator import CoapCoordinator
from .entity import CoapEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP switch entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapSwitch(coordinator, resource, entry) for resource in coordinator.switches
    )


class CoapSwitch(CoapEntity, SwitchEntity):
    """A switch entity backed by a CoAP observable resource with action endpoints."""

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
        self._attr_assumed_state = False
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on the switch."""
        self._attr_is_on = True
        self._attr_assumed_state = True
        self.async_write_ha_state()
        path = self._resource.path
        self.hass.async_create_background_task(
            self._post_and_confirm(path, True),
            name=f"coap_post_{path}",
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off the switch."""
        self._attr_is_on = False
        self._attr_assumed_state = True
        self.async_write_ha_state()
        path = self._resource.path
        self.hass.async_create_background_task(
            self._post_and_confirm(path, False),
            name=f"coap_post_{path}",
        )

    async def _post_and_confirm(self, path: str, value: bool) -> None:
        try:
            payload = cbor2.dumps({SENML_VB: value})
            data = await self._coordinator.async_post(path, payload)
            if data is not None:
                self._attr_is_on = bool(data.get("value"))
                self._attr_assumed_state = False
                self.async_write_ha_state()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("POST to %s failed: %s", path, err)
            if data := self._coordinator.get_state(self._resource.name):
                self._attr_is_on = bool(data.get("value"))
                self._attr_assumed_state = False
                self.async_write_ha_state()
