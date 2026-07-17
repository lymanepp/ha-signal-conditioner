"""Config flow for Normify."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import slugify

from .calibration import (
    InvalidDataPointsError,
    PolynomialCalibration,
    format_data_points_text,
    parse_data_points_text,
)
from .const import (
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_HIDE_SOURCE,
    CONF_PRECISION,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    DOMAIN,
    MAX_DEGREE,
)


def _form_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    """Build the user/reconfigure form with optional suggested values."""
    defaults = defaults or {}
    data_points_text = defaults.get(CONF_DATA_POINTS_TEXT)
    if data_points_text is None and defaults.get(CONF_DATA_POINTS):
        data_points_text = format_data_points_text(defaults[CONF_DATA_POINTS])

    return vol.Schema(
        {
            vol.Required(
                CONF_NAME, default=defaults.get(CONF_NAME, "Normified sensor")
            ): TextSelector(),
            vol.Required(
                CONF_SOURCE, default=defaults.get(CONF_SOURCE)
            ): EntitySelector(EntitySelectorConfig(domain=[SENSOR_DOMAIN])),
            vol.Optional(
                CONF_ATTRIBUTE, default=defaults.get(CONF_ATTRIBUTE, "")
            ): TextSelector(),
            vol.Required(
                CONF_DATA_POINTS_TEXT,
                default=data_points_text or "0, 0\n1, 1",
            ): TextSelector(TextSelectorConfig(multiline=True)),
            vol.Required(
                CONF_DEGREE, default=defaults.get(CONF_DEGREE, DEFAULT_DEGREE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1,
                    max=MAX_DEGREE,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_PRECISION,
                default=defaults.get(CONF_PRECISION, DEFAULT_PRECISION),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=0,
                    max=12,
                    step=1,
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Optional(
                CONF_UNIT_OF_MEASUREMENT,
                default=defaults.get(CONF_UNIT_OF_MEASUREMENT, ""),
            ): TextSelector(),
            vol.Optional(
                CONF_DEVICE_CLASS,
                default=defaults.get(CONF_DEVICE_CLASS, ""),
            ): TextSelector(),
            vol.Optional(
                "state_class", default=defaults.get("state_class", "")
            ): TextSelector(),
            vol.Required(
                CONF_HIDE_SOURCE,
                default=defaults.get(CONF_HIDE_SOURCE, False),
            ): BooleanSelector(),
        }
    )


def _normalize_form_input(user_input: Mapping[str, Any]) -> dict[str, Any]:
    """Convert UI values into persisted config-entry data."""
    data = dict(user_input)

    attribute = str(data.get(CONF_ATTRIBUTE, "")).strip()
    if attribute:
        data[CONF_ATTRIBUTE] = attribute
    else:
        data.pop(CONF_ATTRIBUTE, None)

    for optional_text in (
        CONF_UNIT_OF_MEASUREMENT,
        CONF_DEVICE_CLASS,
        "state_class",
    ):
        value = str(data.get(optional_text, "")).strip()
        if value:
            data[optional_text] = value
        else:
            data.pop(optional_text, None)

    data[CONF_DEGREE] = int(data[CONF_DEGREE])
    data[CONF_PRECISION] = int(data[CONF_PRECISION])
    data[CONF_DATA_POINTS] = [
        list(pair) for pair in parse_data_points_text(data.pop(CONF_DATA_POINTS_TEXT))
    ]

    if data.get(CONF_ATTRIBUTE) and data.get(CONF_HIDE_SOURCE):
        raise InvalidDataPointsError(
            "attribute and hide_source cannot be used together"
        )

    PolynomialCalibration.fit(
        data[CONF_DATA_POINTS],
        degree=data[CONF_DEGREE],
        precision=data[CONF_PRECISION],
    )
    return data


class NormifyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Normify."""

    VERSION = 1
    MINOR_VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create a Normify entry through the UI."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = _normalize_form_input(user_input)
            except InvalidDataPointsError:
                errors["base"] = "invalid_calibration"
            else:
                unique_id = slugify(data[CONF_NAME])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_NAME], data=data)

        return self.async_show_form(
            step_id="user", data_schema=_form_schema(user_input), errors=errors
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        """Import one YAML-defined Normify sensor."""
        unique_id = str(user_input.pop(CONF_UNIQUE_ID))
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=user_input)
        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an existing Normify entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                data = _normalize_form_input(user_input)
            except InvalidDataPointsError:
                errors["base"] = "invalid_calibration"
            else:
                await self.async_set_unique_id(entry.unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    title=data[CONF_NAME],
                    data=data,
                )

        defaults = user_input if user_input is not None else entry.data
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_form_schema(defaults),
            errors=errors,
        )
