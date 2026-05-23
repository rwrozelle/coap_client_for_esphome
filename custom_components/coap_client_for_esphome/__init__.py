"""The CoAP Client integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er

from .const import CONF_OSCORE, CONF_OSCORE_SEQ_THRESHOLD, RT_ACTION, RT_DEVICE
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

type CoapClientConfigEntry = ConfigEntry[CoapCoordinator]


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
        if r.resource_type not in (RT_ACTION, RT_DEVICE)
    }
    ent_reg = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(ent_reg, entry.entry_id):
        suffix = entity_entry.unique_id.removeprefix(f"{unique_id_prefix}_")
        if suffix != entity_entry.unique_id and suffix not in current_names:
            ent_reg.async_remove(entity_entry.entity_id)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_observations()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: CoapClientConfigEntry) -> bool:
    """Unload a CoAP Client config entry."""
    coordinator: CoapCoordinator = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    await coordinator.async_teardown()
    return unload_ok
