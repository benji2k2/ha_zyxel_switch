"""Config flow for the Zyxel Switch integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    CONF_COMMUNITY,
    CONF_SCAN_INTERVAL,
    DEFAULT_COMMUNITY,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .device import SwitchInfo, ZyxelSwitch
from .snmp import SnmpError, SnmpTimeout

_LOGGER = logging.getLogger(__name__)


def _connection_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): TextSelector(),
            vol.Required(
                CONF_COMMUNITY, default=defaults.get(CONF_COMMUNITY, DEFAULT_COMMUNITY)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)): NumberSelector(
                NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
            ),
        }
    )


async def _async_read_info(user_input: dict[str, Any]) -> tuple[SwitchInfo | None, str | None]:
    """Try the connection. Returns the switch info or an error key."""
    client = ZyxelSwitch(user_input[CONF_HOST], user_input[CONF_COMMUNITY], user_input[CONF_PORT])
    try:
        return await client.async_get_info(), None
    except SnmpTimeout:
        return None, "cannot_connect"
    except SnmpError:
        return None, "invalid_response"
    except OSError:
        return None, "invalid_host"
    except Exception:
        _LOGGER.exception("Unexpected error while contacting %s", user_input[CONF_HOST])
        return None, "unknown"
    finally:
        client.close()


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_COMMUNITY: str(user_input[CONF_COMMUNITY]),
        CONF_PORT: int(user_input[CONF_PORT]),
    }


def _unique_id(info: SwitchInfo, host: str) -> str:
    return info.serial or host


class ZyxelSwitchConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a switch by address and SNMP community."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean(user_input)
            info, error = await _async_read_info(user_input)
            if info is not None:
                await self.async_set_unique_id(_unique_id(info, user_input[CONF_HOST]))
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(title=info.name, data=user_input)
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            user_input = _clean(user_input)
            info, error = await _async_read_info(user_input)
            if info is not None:
                await self.async_set_unique_id(_unique_id(info, user_input[CONF_HOST]))
                self._abort_if_unique_id_mismatch(reason="wrong_switch")
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            errors["base"] = error or "unknown"
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> ZyxelSwitchOptionsFlow:  # noqa: ANN001
        return ZyxelSwitchOptionsFlow()


class ZyxelSwitchOptionsFlow(OptionsFlow):
    """Polling interval."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        current = self.config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SCAN_INTERVAL, default=current): NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL,
                            max=MAX_SCAN_INTERVAL,
                            step=1,
                            unit_of_measurement="s",
                            mode=NumberSelectorMode.BOX,
                        )
                    )
                }
            ),
        )
