"""Button platform for the CoAP Client integration."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .coordinator import CoapCoordinator
from .entity import CoapEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP button entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapButton(coordinator, resource, entry) for resource in coordinator.buttons
    )


class CoapButton(CoapEntity, ButtonEntity):
    """A button entity that sends a CoAP POST to its resource path."""

    async def async_press(self) -> None:
        """Send a CoAP POST to trigger the button."""
        await self._coordinator.async_post(self._resource.path)
