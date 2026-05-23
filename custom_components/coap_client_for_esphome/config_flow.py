"""Config flow for the CoAP Client integration."""

import logging
from typing import Any

import aiocoap
import cbor2
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .const import (
    CONF_ID_CONTEXT,
    CONF_MASTER_SALT,
    CONF_MASTER_SECRET,
    CONF_OSCORE,
    CONF_RECIPIENT_ID,
    CONF_SENDER_ID,
    DEFAULT_PORT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
    }
)

STEP_OSCORE_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_MASTER_SECRET, default=""): str,
        vol.Optional(CONF_MASTER_SALT, default=""): str,
        vol.Optional(CONF_SENDER_ID, default=""): str,
        vol.Optional(CONF_RECIPIENT_ID, default=""): str,
        vol.Optional(CONF_ID_CONTEXT, default=""): str,
    }
)


def _clean_hex(value: str) -> str:
    return value.strip().replace(" ", "").replace(":", "").lower()


def _validate_hex(value: str) -> bool:
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    else:
        return True


class CoapClientConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for CoAP Client."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._host: str = ""
        self._port: int = DEFAULT_PORT
        self._device_name: str = ""
        self._oscore_required: bool = False

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the user step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            try:
                device_name, oscore_required = await _fetch_device_info(host, port)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(device_name)
                self._abort_if_unique_id_configured()
                self._host = host
                self._port = port
                self._device_name = device_name
                self._oscore_required = oscore_required
                if oscore_required:
                    return await self.async_step_oscore()
                return self.async_create_entry(
                    title=self._device_name,
                    data={CONF_HOST: self._host, CONF_PORT: self._port},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle zeroconf discovery."""
        host = str(discovery_info.ip_address)
        port = discovery_info.port or DEFAULT_PORT
        try:
            device_name, oscore_required = await _fetch_device_info(host, port)
        except Exception:  # noqa: BLE001
            return self.async_abort(reason="cannot_connect")

        await self.async_set_unique_id(device_name)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self._host = host
        self._port = port
        self._device_name = device_name
        self._oscore_required = oscore_required
        self.context["title_placeholders"] = {"name": device_name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle zeroconf confirmation."""
        if user_input is not None:
            if self._oscore_required:
                return await self.async_step_oscore()
            return self.async_create_entry(
                title=self._device_name,
                data={CONF_HOST: self._host, CONF_PORT: self._port},
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={"name": self._device_name},
        )

    async def async_step_oscore(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle OSCORE credential entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            master_secret = _clean_hex(user_input.get(CONF_MASTER_SECRET, ""))
            if not master_secret:
                return self.async_create_entry(
                    title=self._device_name,
                    data={CONF_HOST: self._host, CONF_PORT: self._port},
                )

            master_salt = _clean_hex(user_input.get(CONF_MASTER_SALT, ""))
            sender_id = _clean_hex(user_input.get(CONF_SENDER_ID, ""))
            recipient_id = _clean_hex(user_input.get(CONF_RECIPIENT_ID, ""))
            id_context = _clean_hex(user_input.get(CONF_ID_CONTEXT, ""))

            for field, value in (
                (CONF_MASTER_SECRET, master_secret),
                (CONF_MASTER_SALT, master_salt),
                (CONF_SENDER_ID, sender_id),
                (CONF_RECIPIENT_ID, recipient_id),
                (CONF_ID_CONTEXT, id_context),
            ):
                if value and not _validate_hex(value):
                    errors[field] = "invalid_hex"

            if not sender_id:
                errors[CONF_SENDER_ID] = "oscore_field_required"
            if not recipient_id:
                errors[CONF_RECIPIENT_ID] = "oscore_field_required"

            if not errors and sender_id == recipient_id:
                errors["base"] = "oscore_ids_same"

            if not errors:
                return self.async_create_entry(
                    title=self._device_name,
                    data={
                        CONF_HOST: self._host,
                        CONF_PORT: self._port,
                        CONF_OSCORE: {
                            CONF_MASTER_SECRET: master_secret,
                            CONF_MASTER_SALT: master_salt,
                            CONF_SENDER_ID: sender_id,
                            CONF_RECIPIENT_ID: recipient_id,
                            CONF_ID_CONTEXT: id_context,
                        },
                    },
                )

        return self.async_show_form(
            step_id="oscore",
            data_schema=STEP_OSCORE_SCHEMA,
            errors=errors,
        )


async def _fetch_device_info(host: str, port: int) -> tuple[str, bool]:
    """Connect to a CoAP server and return its device name and OSCORE requirement from /info."""
    host_uri = f"[{host}]" if ":" in host and not host.startswith("[") else host
    uri = f"coap://{host_uri}:{port}/info"
    ctx = await aiocoap.Context.create_client_context()
    try:
        response = await ctx.request(
            aiocoap.Message(mtype=aiocoap.NON, code=aiocoap.GET, uri=uri)
        ).response
        raw = cbor2.loads(response.payload)
        if isinstance(raw, dict):
            name = str(raw.get("friendly_name") or raw.get("name") or host)
            oscore = bool(raw.get("oscore", False))
            return name, oscore
        return host, False
    finally:
        await ctx.shutdown()
