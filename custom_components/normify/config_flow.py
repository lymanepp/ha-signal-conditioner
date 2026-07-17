"""Config flow for Normify."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ATTRIBUTE, CONF_NAME, CONF_SOURCE, CONF_UNIQUE_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    BooleanSelector,
    DurationSelector,
    DurationSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)
from homeassistant.util import slugify

from .calibration import (
    InvalidDataPointsError,
    format_data_points_text,
    parse_data_points_text,
)
from .config import pipeline_config_from_data
from .const import (
    CONF_ALPHA,
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_EXPONENTIAL_ALPHA,
    CONF_HIDE_SOURCE,
    CONF_MAXIMUM,
    CONF_MAXIMUM_INTERVAL,
    CONF_MEDIAN_WINDOW,
    CONF_METHOD,
    CONF_MINIMUM,
    CONF_MINIMUM_CHANGE,
    CONF_MINIMUM_INTERVAL,
    CONF_MOVING_AVERAGE_WINDOW,
    CONF_OFFSET,
    CONF_PRECISION,
    CONF_SCALE,
    CONF_STALE_AFTER,
    CONF_WINDOW,
    DEFAULT_DEGREE,
    DEFAULT_OFFSET,
    DEFAULT_PRECISION,
    DEFAULT_SCALE,
    DOMAIN,
    MAX_DEGREE,
    MAX_WINDOW_SIZE,
    SMOOTHING_EXPONENTIAL,
    SMOOTHING_MEDIAN,
    SMOOTHING_MOVING_AVERAGE,
    SMOOTHING_NONE,
)
from .pipeline import PipelineConfigurationError


def _duration_default(seconds: object) -> dict[str, float]:
    return {"seconds": float(cast(Any, seconds or 0))}


def _suggested_optional(key: str, defaults: Mapping[str, Any]) -> vol.Optional:
    if key in defaults and defaults[key] is not None:
        return vol.Optional(key, description={"suggested_value": defaults[key]})
    return vol.Optional(key)


def _basic_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SOURCE, default=defaults.get(CONF_SOURCE)
            ): EntitySelector(EntitySelectorConfig(domain=[SENSOR_DOMAIN])),
            vol.Optional(
                CONF_NAME, default=defaults.get(CONF_NAME, "")
            ): TextSelector(),
            vol.Optional(
                CONF_ATTRIBUTE, default=defaults.get(CONF_ATTRIBUTE, "")
            ): TextSelector(),
            vol.Required(
                CONF_HIDE_SOURCE, default=defaults.get(CONF_HIDE_SOURCE, False)
            ): BooleanSelector(),
        }
    )


def _conditioning_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    points = format_data_points_text(defaults.get(CONF_DATA_POINTS, []))
    method = SMOOTHING_NONE
    window = 3
    alpha = 0.25
    if int(defaults.get(CONF_MEDIAN_WINDOW, 1)) > 1:
        method, window = SMOOTHING_MEDIAN, int(defaults[CONF_MEDIAN_WINDOW])
    elif int(defaults.get(CONF_MOVING_AVERAGE_WINDOW, 1)) > 1:
        method, window = (
            SMOOTHING_MOVING_AVERAGE,
            int(defaults[CONF_MOVING_AVERAGE_WINDOW]),
        )
    elif defaults.get(CONF_EXPONENTIAL_ALPHA) is not None:
        method, alpha = SMOOTHING_EXPONENTIAL, float(defaults[CONF_EXPONENTIAL_ALPHA])
    number = NumberSelectorConfig(mode=NumberSelectorMode.BOX)
    return vol.Schema(
        {
            _suggested_optional(CONF_MINIMUM, defaults): NumberSelector(number),
            _suggested_optional(CONF_MAXIMUM, defaults): NumberSelector(number),
            vol.Optional(CONF_DATA_POINTS_TEXT, default=points): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                CONF_DEGREE, default=defaults.get(CONF_DEGREE, DEFAULT_DEGREE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_DEGREE, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(
                CONF_SCALE, default=defaults.get(CONF_SCALE, DEFAULT_SCALE)
            ): NumberSelector(number),
            vol.Required(
                CONF_OFFSET, default=defaults.get(CONF_OFFSET, DEFAULT_OFFSET)
            ): NumberSelector(number),
            vol.Required(CONF_METHOD, default=method): SelectSelector(
                SelectSelectorConfig(
                    options=[
                        SMOOTHING_NONE,
                        SMOOTHING_MEDIAN,
                        SMOOTHING_MOVING_AVERAGE,
                        SMOOTHING_EXPONENTIAL,
                    ],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="smoothing_method",
                )
            ),
            vol.Required(CONF_WINDOW, default=window): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_WINDOW_SIZE, step=1, mode=NumberSelectorMode.BOX
                )
            ),
            vol.Required(CONF_ALPHA, default=alpha): NumberSelector(
                NumberSelectorConfig(
                    min=0.01, max=1, step=0.01, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _publication_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    print(defaults)
    return vol.Schema(
        {
            vol.Required(
                CONF_PRECISION, default=defaults.get(CONF_PRECISION, DEFAULT_PRECISION)
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=12, step=1, mode=NumberSelectorMode.BOX)
            ),
            _suggested_optional(CONF_MINIMUM_CHANGE, defaults): NumberSelector(
                NumberSelectorConfig(min=0, mode=NumberSelectorMode.BOX)
            ),
            vol.Required(
                CONF_MINIMUM_INTERVAL,
                default=_duration_default(defaults.get(CONF_MINIMUM_INTERVAL, 0)),
            ): DurationSelector(DurationSelectorConfig(enable_day=True)),
            vol.Required(
                CONF_MAXIMUM_INTERVAL,
                default=_duration_default(defaults.get(CONF_MAXIMUM_INTERVAL, 0)),
            ): DurationSelector(DurationSelectorConfig(enable_day=True)),
            vol.Required(
                CONF_STALE_AFTER,
                default=_duration_default(defaults.get(CONF_STALE_AFTER, 0)),
            ): DurationSelector(DurationSelectorConfig(enable_day=True)),
        }
    )


def _normalize_basic(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> dict[str, Any]:
    data = dict(user_input)
    source = str(data[CONF_SOURCE])
    name = str(data.get(CONF_NAME, "")).strip()
    if not name:
        state = hass.states.get(source)
        name = (
            str(state.attributes.get("friendly_name"))
            if state and state.attributes.get("friendly_name")
            else source.split(".", 1)[1].replace("_", " ").title()
        )
        for suffix in (" Raw", " Unfiltered", " Uncalibrated", " Source"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
    data[CONF_NAME] = name
    attribute = str(data.get(CONF_ATTRIBUTE, "")).strip()
    if attribute:
        data[CONF_ATTRIBUTE] = attribute
    else:
        data.pop(CONF_ATTRIBUTE, None)
    if data.get(CONF_ATTRIBUTE) and data.get(CONF_HIDE_SOURCE):
        raise PipelineConfigurationError(
            "attribute and hide_source cannot be used together"
        )
    return data


def _normalize_conditioning(user_input: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_MINIMUM, CONF_MAXIMUM):
        if data.get(key) in (None, ""):
            data.pop(key, None)
    points_text = str(data.pop(CONF_DATA_POINTS_TEXT, "")).strip()
    data[CONF_DATA_POINTS] = (
        [list(pair) for pair in parse_data_points_text(points_text)]
        if points_text
        else []
    )
    data[CONF_DEGREE] = int(data[CONF_DEGREE])
    data[CONF_SCALE] = float(data[CONF_SCALE])
    data[CONF_OFFSET] = float(data[CONF_OFFSET])
    method = data.pop(CONF_METHOD)
    window = int(data.pop(CONF_WINDOW))
    alpha = float(data.pop(CONF_ALPHA))
    data[CONF_MEDIAN_WINDOW] = window if method == SMOOTHING_MEDIAN else 1
    data[CONF_MOVING_AVERAGE_WINDOW] = (
        window if method == SMOOTHING_MOVING_AVERAGE else 1
    )
    if method == SMOOTHING_EXPONENTIAL:
        data[CONF_EXPONENTIAL_ALPHA] = alpha
    else:
        data.pop(CONF_EXPONENTIAL_ALPHA, None)
    return data


def _normalize_publication(user_input: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    data[CONF_PRECISION] = int(data[CONF_PRECISION])
    if data.get(CONF_MINIMUM_CHANGE) in (None, ""):
        data.pop(CONF_MINIMUM_CHANGE, None)
    for key in (CONF_MINIMUM_INTERVAL, CONF_MAXIMUM_INTERVAL, CONF_STALE_AFTER):
        data[key] = timedelta(**data[key]).total_seconds()
    return data


class NormifyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the concise Normify config flow."""

    VERSION = 2
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._flow_data: dict[str, Any] = {}
        self._defaults: Mapping[str, Any] = {}
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_basic("user", user_input, False)

    async def async_step_conditioning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_conditioning("conditioning", user_input)

    async def async_step_publication(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_publication("publication", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._reconfigure_entry is None:
            self._reconfigure_entry = self._get_reconfigure_entry()
            self._defaults = self._reconfigure_entry.data
        return await self._async_basic("reconfigure", user_input, True)

    async def async_step_reconfigure_conditioning(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_conditioning("reconfigure_conditioning", user_input)

    async def async_step_reconfigure_publication(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_publication("reconfigure_publication", user_input)

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        unique_id = str(user_input.pop(CONF_UNIQUE_ID))
        try:
            pipeline_config_from_data(user_input)
        except PipelineConfigurationError:
            return self.async_abort(reason="invalid_import")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=user_input)
        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    async def _async_basic(
        self, step_id: str, user_input: dict[str, Any] | None, reconfigure: bool
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                self._flow_data.update(_normalize_basic(self.hass, user_input))
            except PipelineConfigurationError:
                errors["base"] = "invalid_configuration"
            else:
                return await (
                    self.async_step_reconfigure_conditioning()
                    if reconfigure
                    else self.async_step_conditioning()
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=_basic_schema(user_input or self._defaults),
            errors=errors,
        )

    async def _async_conditioning(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = _normalize_conditioning(user_input)
                pipeline_config_from_data({**self._flow_data, **normalized})
            except (InvalidDataPointsError, PipelineConfigurationError):
                errors["base"] = "invalid_configuration"
            else:
                self._flow_data.update(normalized)
                return await (
                    self.async_step_reconfigure_publication()
                    if self._reconfigure_entry
                    else self.async_step_publication()
                )
        return self.async_show_form(
            step_id=step_id,
            data_schema=_conditioning_schema(user_input or self._defaults),
            errors=errors,
        )

    async def _async_publication(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = _normalize_publication(user_input)
                data = {**self._flow_data, **normalized}
                pipeline_config_from_data(data)
            except PipelineConfigurationError:
                errors["base"] = "invalid_configuration"
            else:
                if self._reconfigure_entry:
                    await self.async_set_unique_id(self._reconfigure_entry.unique_id)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        self._reconfigure_entry, title=data[CONF_NAME], data=data
                    )
                unique_id = slugify(data[CONF_NAME])
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id=step_id,
            data_schema=_publication_schema(user_input or self._defaults),
            errors=errors,
        )
