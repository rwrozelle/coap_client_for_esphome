"""Button platform for the CoAP Client integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .const import DOMAIN
from .coordinator import CoapCoordinator
from .entity import CoapEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP button entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        CoapButton(coordinator, resource, entry) for resource in coordinator.buttons
    ]
    entities.append(CoapResubscribeButton(coordinator, entry))
    async_add_entities(entities)


class CoapButton(CoapEntity, ButtonEntity):
    """A button entity that sends a CoAP POST to its resource path."""

    async def async_press(self) -> None:
        """Send a CoAP POST to trigger the button."""
        await self._coordinator.async_post(self._resource.path)


class CoapResubscribeButton(ButtonEntity):
    """A button that immediately refreshes all CoAP observe subscriptions."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Refresh subscriptions"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: CoapCoordinator, entry: CoapClientConfigEntry) -> None:
        self._coordinator = coordinator
        unique_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{unique_id}_resubscribe"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_id)},
            name=coordinator.device_info.friendly_name or coordinator.device_info.name,
            manufacturer="ESPHome",
            model=coordinator.device_info.model,
            sw_version=coordinator.device_info.version,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self._coordinator.subscribe_availability(self._handle_availability)
        )
        if self._coordinator.available:
            self._attr_available = True

    @callback
    def _handle_availability(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()

    async def async_press(self) -> None:
        self._coordinator.async_resubscribe()
