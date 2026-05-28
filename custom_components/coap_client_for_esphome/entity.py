"""Base entity for the CoAP Client integration."""

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import CoapClientConfigEntry
from .const import DOMAIN
from .coordinator import CoapCoordinator, CoapResource


class CoapEntity(Entity):
    """Base class for all CoAP Client entities."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CoapCoordinator,
        resource: CoapResource,
        entry: CoapClientConfigEntry,
    ) -> None:
        """Initialize the CoAP entity."""
        self._coordinator = coordinator
        self._resource = resource
        unique_id = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{unique_id}_{resource.name}"
        self._attr_name = resource.title or resource.path
        self._attr_available = False
        if resource.device_index > 0:
            devices = coordinator.device_info.devices
            if resource.device_index <= len(devices):
                dev = devices[resource.device_index - 1]
                sub_unique = f"{unique_id}_dv{resource.device_index}"
                area_idx = dev.get("area")
                area_name: str | None = None
                if isinstance(area_idx, int) and area_idx > 0:
                    areas = coordinator.device_info.areas
                    if area_idx <= len(areas):
                        area_name = areas[area_idx - 1].get("name")
                self._attr_device_info = DeviceInfo(
                    identifiers={(DOMAIN, sub_unique)},
                    name=dev.get("name", f"Device {resource.device_index}"),
                    manufacturer="ESPHome",
                    suggested_area=area_name,
                )
            else:
                self._attr_device_info = DeviceInfo(
                    identifiers={(DOMAIN, unique_id)},
                    name=coordinator.device_info.friendly_name
                    or coordinator.device_info.name,
                    manufacturer="ESPHome",
                    model=coordinator.device_info.model,
                    sw_version=coordinator.device_info.version,
                )
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, unique_id)},
                name=coordinator.device_info.friendly_name
                or coordinator.device_info.name,
                manufacturer="ESPHome",
                model=coordinator.device_info.model,
                sw_version=coordinator.device_info.version,
            )

    async def async_added_to_hass(self) -> None:
        """Register availability callback."""
        self.async_on_remove(
            self._coordinator.subscribe_availability(self._handle_availability)
        )
        if self._coordinator.available:
            self._attr_available = True

    @callback
    def _handle_availability(self, available: bool) -> None:
        self._attr_available = available
        self.async_write_ha_state()
