"""The CoAP Client integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_OSCORE, CONF_OSCORE_SEQ_THRESHOLD, CONF_SUBSCRIBE_LOGS, DOMAIN, RT_ACTION, RT_DEVICE, RT_LOG
from .coordinator import CoapCoordinator

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.VALVE,
]

CoapClientConfigEntry = ConfigEntry[CoapCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: CoapClientConfigEntry) -> bool:
    """Set up CoAP Client from a config entry."""
    oscore_config = entry.data.get(CONF_OSCORE)

    def _save_oscore_seq_threshold(threshold: int) -> None:
        updated = {**entry.data[CONF_OSCORE], CONF_OSCORE_SEQ_THRESHOLD: threshold}
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_OSCORE: updated}
        )

    coordinator = CoapCoordinator(
        hass,
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        oscore_config=oscore_config,
        oscore_save_callback=_save_oscore_seq_threshold if oscore_config else None,
        entry_id=entry.entry_id,
        subscribe_logs=entry.options.get(CONF_SUBSCRIBE_LOGS, False),
    )
    try:
        await coordinator.async_setup()
    except Exception as err:
        await coordinator.async_teardown()
        raise ConfigEntryNotReady(
            f"Cannot connect to CoAP server at {entry.data[CONF_HOST]}: {err}"
        ) from err

    entry.runtime_data = coordinator

    # Remove entity registry entries for resources that no longer exist on the device.
    unique_id_prefix = entry.unique_id or entry.entry_id
    current_names = {
        r.name
        for r in coordinator.resources
        if r.resource_type not in (RT_ACTION, RT_DEVICE, RT_LOG)
    }
    ent_reg = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        suffix = entity_entry.unique_id.removeprefix(f"{unique_id_prefix}_")
        if suffix != entity_entry.unique_id and suffix not in current_names:
            ent_reg.async_remove(entity_entry.entity_id)

    _setup_device_registry(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_observations()
    return True


def _setup_device_registry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    coordinator: CoapCoordinator,
) -> None:
    """Register the main device and sub-devices, linking them via via_device_id."""
    unique_id = entry.unique_id or entry.entry_id
    dev_info = coordinator.device_info
    dev_reg = dr.async_get(hass)

    main_entry = dev_reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, unique_id)},
        name=dev_info.friendly_name or dev_info.name,
        manufacturer="ESPHome",
        model=dev_info.model,
        sw_version=dev_info.version,
    )

    areas = dev_info.areas
    for idx, dev in enumerate(dev_info.devices, start=1):
        sub_unique = f"{unique_id}_dv{idx}"
        area_name: str | None = None
        area_idx = dev.get("area")
        if isinstance(area_idx, int) and area_idx > 0 and area_idx <= len(areas):
            area_name = areas[area_idx - 1].get("name")
        sub_entry = dev_reg.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, sub_unique)},
            name=dev.get("name", f"Device {idx}"),
            manufacturer="ESPHome",
            suggested_area=area_name,
        )
        dev_reg.async_update_device(sub_entry.id, via_device_id=main_entry.id)


async def async_unload_entry(hass: HomeAssistant, entry: CoapClientConfigEntry) -> bool:
    """Unload a CoAP Client config entry."""
    coordinator: CoapCoordinator = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await coordinator.async_teardown()
    return unload_ok
