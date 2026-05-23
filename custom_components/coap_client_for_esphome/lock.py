"""Lock platform for the CoAP Client integration."""

from typing import Any

import cbor2

from homeassistant.components.lock import LockEntity, LockState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import CoapClientConfigEntry
from .const import SENML_VB
from .coordinator import CoapCoordinator
from .entity import CoapEntity

# Maps ESPHome LockState enum int to HA LockState (None = unknown/none)
_ESPHOME_TO_HA_LOCK_STATE: dict[int, LockState | None] = {
    0: None,
    1: LockState.LOCKED,
    2: LockState.UNLOCKED,
    3: LockState.JAMMED,
    4: LockState.LOCKING,
    5: LockState.UNLOCKING,
    6: LockState.OPENING,
    7: LockState.OPEN,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CoapClientConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up CoAP lock entities."""
    coordinator: CoapCoordinator = entry.runtime_data
    async_add_entities(
        CoapLock(coordinator, resource, entry) for resource in coordinator.locks
    )


class CoapLock(CoapEntity, LockEntity):
    """A lock entity backed by a CoAP observable resource with action endpoints."""

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
        ha_state = _ESPHOME_TO_HA_LOCK_STATE.get(int(raw)) if raw is not None else None
        self._attr_is_locked = ha_state == LockState.LOCKED
        self._attr_is_locking = ha_state == LockState.LOCKING
        self._attr_is_unlocking = ha_state == LockState.UNLOCKING
        self._attr_is_jammed = ha_state == LockState.JAMMED
        self._attr_is_open = ha_state == LockState.OPEN
        self._attr_is_opening = ha_state == LockState.OPENING
        self._attr_assumed_state = False

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the lock."""
        self._attr_is_locking = True
        self._attr_is_locked = False
        self._attr_is_unlocking = False
        self._attr_assumed_state = True
        self.async_write_ha_state()
        path = self._resource.path
        self.hass.async_create_background_task(
            self._post_and_confirm(path, True),
            name=f"coap_post_{path}",
        )

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock."""
        self._attr_is_unlocking = True
        self._attr_is_locked = False
        self._attr_is_locking = False
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
                self._apply_state(data)
                self.async_write_ha_state()
        except Exception:  # noqa: BLE001
            pass
