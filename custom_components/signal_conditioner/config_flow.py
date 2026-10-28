"""Config flow for Signal Conditioner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.selector import (
    BooleanSelector,
    DurationSelector,
    DurationSelectorConfig,
    EntitySelector,
    EntitySelectorConfig,
    IconSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

try:
    # Home Assistant replaced voluptuous with probatio, an API-compatible
    # drop-in (2026.10 depends on probatio; 2026.8 and earlier, including the
    # 2026.7.2 minimum in hacs.json, only on voluptuous).
    import probatio as vol
except ImportError:
    import voluptuous as vol  # type: ignore[no-redef]

from .calibration import (
    CalibrationError,
    PolynomialCalibration,
    format_data_points_text,
    parse_data_points_text,
)
from .config import pipeline_config_from_data
from .const import (
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_HIDE_SOURCE,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_ROUNDING,
    CONF_STATE_CLASS,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    DOMAIN,
    MAX_DEGREE,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import MAX_WINDOW_SECONDS, PipelineConfigurationError

_METADATA_KEYS = (
    CONF_UNIT_OF_MEASUREMENT,
    CONF_DEVICE_CLASS,
    CONF_STATE_CLASS,
    CONF_ICON,
)


def _duration_number(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, str | int | float):
        return float(value)
    raise ValueError(f"Invalid duration value: {value!r}")


def _duration_default(value: object) -> dict[str, float]:
    if isinstance(value, Mapping):
        return {
            str(part): _duration_number(amount)
            for part, amount in value.items()
            if part in {"days", "hours", "minutes", "seconds"}
        } or {"seconds": 0.0}
    return {"seconds": _duration_number(value)}


def _suggested_optional(key: str, defaults: Mapping[str, Any]) -> vol.Optional:
    if key in defaults and defaults[key] is not None:
        return vol.Optional(key, description={"suggested_value": defaults[key]})
    return vol.Optional(key)


def _source_unique_id(data: Mapping[str, Any]) -> str:
    source = str(data[CONF_SOURCE])
    attribute = str(data.get(CONF_ATTRIBUTE, "")).strip()
    return f"{source}::{attribute}" if attribute else source


def _source_schema(
    defaults: Mapping[str, Any], *, reconfigure: bool = False
) -> vol.Schema:
    fields: dict[vol.Marker, Any] = {}
    if not reconfigure:
        fields[vol.Required(CONF_SOURCE, default=defaults.get(CONF_SOURCE))] = (
            EntitySelector(EntitySelectorConfig(domain=[SENSOR_DOMAIN]))
        )
        attribute_key = vol.Optional(
            CONF_ATTRIBUTE, default=defaults.get(CONF_ATTRIBUTE, "")
        )
        fields[attribute_key] = TextSelector()
    fields.update(
        {
            vol.Optional(
                CONF_NAME, default=defaults.get(CONF_NAME, "")
            ): TextSelector(),
            vol.Required(
                CONF_HIDE_SOURCE, default=defaults.get(CONF_HIDE_SOURCE, False)
            ): BooleanSelector(),
            _suggested_optional(CONF_UNIT_OF_MEASUREMENT, defaults): TextSelector(),
            _suggested_optional(CONF_DEVICE_CLASS, defaults): SelectSelector(
                SelectSelectorConfig(
                    options=["", *(item.value for item in SensorDeviceClass)],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            _suggested_optional(CONF_STATE_CLASS, defaults): SelectSelector(
                SelectSelectorConfig(
                    options=["", *(item.value for item in SensorStateClass)],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            _suggested_optional(CONF_ICON, defaults): IconSelector(),
            vol.Required(
                CONF_ENABLE_LIMITS,
                default=CONF_VALUE_LIMITS in defaults,
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_CALIBRATION,
                default=CONF_CALIBRATION in defaults,
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_WINDOW,
                default=CONF_WINDOW in defaults,
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_ROUNDING,
                default=CONF_ROUNDING in defaults,
            ): BooleanSelector(),
        }
    )
    return vol.Schema(fields)


def _value_limits_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    defaults = _section(defaults, CONF_VALUE_LIMITS)
    number = NumberSelectorConfig(mode=NumberSelectorMode.BOX)
    return vol.Schema(
        {
            _suggested_optional(CONF_MINIMUM, defaults): NumberSelector(number),
            _suggested_optional(CONF_MAXIMUM, defaults): NumberSelector(number),
        }
    )


def _calibration_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    defaults = _section(defaults, CONF_CALIBRATION)
    points = format_data_points_text(defaults.get(CONF_DATA_POINTS, []))
    return vol.Schema(
        {
            vol.Required(CONF_DATA_POINTS_TEXT, default=points): TextSelector(
                TextSelectorConfig(multiline=True)
            ),
            vol.Required(
                CONF_DEGREE, default=defaults.get(CONF_DEGREE, DEFAULT_DEGREE)
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=MAX_DEGREE, step=1, mode=NumberSelectorMode.BOX
                )
            ),
        }
    )


def _window_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    defaults = _section(defaults, CONF_WINDOW)
    duration = defaults.get(CONF_DURATION, 60)
    output = str(defaults.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN))
    return vol.Schema(
        {
            vol.Required(
                CONF_DURATION, default=_duration_default(duration)
            ): DurationSelector(DurationSelectorConfig(enable_day=True)),
            vol.Required(CONF_OUTPUT, default=output): SelectSelector(
                SelectSelectorConfig(
                    options=[WINDOW_OUTPUT_MEAN, WINDOW_OUTPUT_LATEST],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="window_output",
                )
            ),
        }
    )


def _rounding_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    defaults = _section(defaults, CONF_ROUNDING)
    return vol.Schema(
        {
            vol.Required(
                CONF_PRECISION, default=defaults.get(CONF_PRECISION, DEFAULT_PRECISION)
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=12, step=1, mode=NumberSelectorMode.BOX)
            )
        }
    )


def _section(defaults: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = defaults.get(key)
    return value if isinstance(value, Mapping) else {}


def _normalize_source(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], set[str]]:
    data = dict(user_input)
    enabled = {
        key
        for key in (
            CONF_ENABLE_LIMITS,
            CONF_ENABLE_CALIBRATION,
            CONF_ENABLE_WINDOW,
            CONF_ENABLE_ROUNDING,
        )
        if bool(data.pop(key, False))
    }
    source = str(data[CONF_SOURCE])
    name = str(data.get(CONF_NAME, "")).strip()
    if not name:
        state = hass.states.get(source)
        name = (
            str(state.attributes.get("friendly_name"))
            if state and state.attributes.get("friendly_name")
            else source.split(".", 1)[1].replace("_", " ").title()
        )
    data[CONF_NAME] = name

    attribute = str(data.get(CONF_ATTRIBUTE, "")).strip()
    if attribute and data.get(CONF_HIDE_SOURCE):
        raise PipelineConfigurationError(
            "attribute and hide_source cannot be used together"
        )
    if attribute:
        data[CONF_ATTRIBUTE] = attribute
    else:
        data.pop(CONF_ATTRIBUTE, None)

    for key in _METADATA_KEYS:
        value = str(data.get(key, "")).strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
    return data, enabled


class SignalConditionerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a conditioned sensor."""

    VERSION = 1
    MINOR_VERSION = 0

    def __init__(self) -> None:
        self._flow_data: dict[str, Any] = {}
        self._defaults: Mapping[str, Any] = {}
        self._enabled: set[str] = set()
        self._completed: set[str] = set()
        self._reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_source("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._reconfigure_entry is None:
            self._reconfigure_entry = self._get_reconfigure_entry()
            self._defaults = self._reconfigure_entry.data
        return await self._async_source("reconfigure", user_input)

    async def async_step_value_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = {
                    key: float(user_input[key])
                    for key in (CONF_MINIMUM, CONF_MAXIMUM)
                    if user_input.get(key) not in (None, "")
                }
                minimum = normalized.get(CONF_MINIMUM)
                maximum = normalized.get(CONF_MAXIMUM)
            except (TypeError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                if minimum is not None and maximum is not None and minimum > maximum:
                    errors["base"] = "invalid_configuration"
                    return self.async_show_form(
                        step_id="value_limits",
                        data_schema=_value_limits_schema(
                            {CONF_VALUE_LIMITS: user_input}
                        ),
                        errors=errors,
                    )
                self._flow_data[CONF_VALUE_LIMITS] = normalized
                self._completed.add(CONF_ENABLE_LIMITS)
                return await self._next_step()
        return self.async_show_form(
            step_id="value_limits",
            data_schema=_value_limits_schema(
                {CONF_VALUE_LIMITS: user_input}
                if user_input is not None
                else self._defaults
            ),
            errors=errors,
        )

    async def async_step_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                points = parse_data_points_text(
                    str(user_input.get(CONF_DATA_POINTS_TEXT, "")).strip()
                )
                normalized: dict[str, Any] = {
                    CONF_DATA_POINTS: [list(pair) for pair in points],
                    CONF_DEGREE: int(user_input.get(CONF_DEGREE, DEFAULT_DEGREE)),
                }
                PolynomialCalibration.fit(points, degree=normalized[CONF_DEGREE])
            except (CalibrationError, TypeError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                self._flow_data[CONF_CALIBRATION] = normalized
                self._completed.add(CONF_ENABLE_CALIBRATION)
                return await self._next_step()
        return self.async_show_form(
            step_id="calibration",
            data_schema=_calibration_schema(
                {CONF_CALIBRATION: user_input}
                if user_input is not None
                else self._defaults
            ),
            errors=errors,
        )

    async def async_step_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:

            def validate_window_seconds(seconds: float) -> None:
                if not 0 < seconds <= MAX_WINDOW_SECONDS:
                    raise ValueError

            try:
                seconds = timedelta(**user_input[CONF_DURATION]).total_seconds()
                validate_window_seconds(seconds)
                normalized: dict[str, Any] = {
                    CONF_DURATION: seconds,
                    CONF_OUTPUT: str(user_input.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN)),
                }
            except (TypeError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                self._flow_data[CONF_WINDOW] = normalized
                self._completed.add(CONF_ENABLE_WINDOW)
                return await self._next_step()
        return self.async_show_form(
            step_id="window",
            data_schema=_window_schema(
                {CONF_WINDOW: user_input} if user_input is not None else self._defaults
            ),
            errors=errors,
        )

    async def async_step_rounding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = {CONF_PRECISION: int(user_input[CONF_PRECISION])}
            except (TypeError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                self._flow_data[CONF_ROUNDING] = normalized
                self._completed.add(CONF_ENABLE_ROUNDING)
                return await self._next_step()
        return self.async_show_form(
            step_id="rounding",
            data_schema=_rounding_schema(
                {CONF_ROUNDING: user_input}
                if user_input is not None
                else self._defaults
            ),
            errors=errors,
        )

    async def async_step_import(self, user_input: dict[str, Any]) -> ConfigFlowResult:
        unique_id = str(user_input.pop(CONF_UNIQUE_ID))
        try:
            pipeline_config_from_data(user_input)
        except PipelineConfigurationError:
            return self.async_abort(reason="invalid_import")
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured(updates=user_input)
        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    async def _async_source(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            source_input = dict(user_input)
            if self._reconfigure_entry is not None:
                source_input[CONF_SOURCE] = self._reconfigure_entry.data[CONF_SOURCE]
                if CONF_ATTRIBUTE in self._reconfigure_entry.data:
                    source_input[CONF_ATTRIBUTE] = self._reconfigure_entry.data[
                        CONF_ATTRIBUTE
                    ]
            try:
                self._flow_data, self._enabled = _normalize_source(
                    self.hass, source_input
                )
            except PipelineConfigurationError:
                errors["base"] = "invalid_configuration"
            else:
                self._completed.clear()
                return await self._next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=_source_schema(
                user_input or self._defaults,
                reconfigure=self._reconfigure_entry is not None,
            ),
            errors=errors,
        )

    async def _next_step(self) -> ConfigFlowResult:
        if (
            CONF_ENABLE_LIMITS in self._enabled
            and CONF_ENABLE_LIMITS not in self._completed
        ):
            return await self.async_step_value_limits()
        if (
            CONF_ENABLE_CALIBRATION in self._enabled
            and CONF_ENABLE_CALIBRATION not in self._completed
        ):
            return await self.async_step_calibration()
        if (
            CONF_ENABLE_WINDOW in self._enabled
            and CONF_ENABLE_WINDOW not in self._completed
        ):
            return await self.async_step_window()
        if (
            CONF_ENABLE_ROUNDING in self._enabled
            and CONF_ENABLE_ROUNDING not in self._completed
        ):
            return await self.async_step_rounding()
        return await self._finish()

    async def _finish(self) -> ConfigFlowResult:
        data = dict(self._flow_data)
        pipeline_config_from_data(data)
        if self._reconfigure_entry:
            return self.async_update_reload_and_abort(
                self._reconfigure_entry, title=data[CONF_NAME], data=data
            )
        await self.async_set_unique_id(_source_unique_id(data))
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=data[CONF_NAME], data=data)
