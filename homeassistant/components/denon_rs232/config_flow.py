"""Config flow for the Denon RS232 integration."""

from __future__ import annotations

import os
from typing import Any

from denon_rs232 import DenonReceiver
from denon_rs232.models import MODELS
import voluptuous as vol

from homeassistant.components.usb import human_readable_device_name, scan_serial_ports
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_DEVICE, CONF_MODEL

from .const import DOMAIN, LOGGER

MODEL_OPTIONS = {key: model.name for key, model in MODELS.items()}

OPTION_PICK_MANUAL = "Enter Manually"


class DenonRS232ConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Denon RS232."""

    VERSION = 1

    _model: str

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            if user_input[CONF_DEVICE] == OPTION_PICK_MANUAL:
                self._model = user_input[CONF_MODEL]
                return await self.async_step_manual()

            self._async_abort_entries_match({CONF_DEVICE: user_input[CONF_DEVICE]})

            model = MODELS[user_input[CONF_MODEL]]
            receiver = DenonReceiver(user_input[CONF_DEVICE], model=model)
            try:
                await receiver.connect()
            except (ConnectionError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await receiver.disconnect()
                return self.async_create_entry(title=model.name, data=user_input)

        ports = await self.hass.async_add_executor_job(get_ports)
        ports[OPTION_PICK_MANUAL] = OPTION_PICK_MANUAL

        if user_input is None and ports:
            user_input = {CONF_DEVICE: next(iter(ports))}

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(
                    {
                        vol.Required(CONF_MODEL): vol.In(MODEL_OPTIONS),
                        vol.Required(CONF_DEVICE): vol.In(ports),
                    }
                ),
                user_input or {},
            ),
            errors=errors,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle a manual port selection."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._async_abort_entries_match({CONF_DEVICE: user_input[CONF_DEVICE]})

            model = MODELS[self._model]
            receiver = DenonReceiver(user_input[CONF_DEVICE], model=model)

            try:
                await receiver.connect()
            except (ConnectionError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"
            else:
                await receiver.disconnect()
                return self.async_create_entry(
                    title=model.name,
                    data={
                        CONF_DEVICE: user_input[CONF_DEVICE],
                        CONF_MODEL: self._model,
                    },
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_DEVICE): str}),
            errors=errors,
        )


def get_ports() -> dict[str, str]:
    """Get available serial ports keyed by their device path."""
    return {
        port.device: human_readable_device_name(
            os.path.realpath(port.device),
            port.serial_number,
            port.manufacturer,
            port.description,
            port.vid,
            port.pid,
        )
        for port in scan_serial_ports()
    }
