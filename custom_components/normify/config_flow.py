"""Config flow for Normify."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
from typing import Any, cast

import voluptuous as vol
from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
)
from homeassistant.components.sensor import (
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
from homeassistant.util import slugify

from .calibration import (
    InvalidDataPointsError,
    format_data_points_text,
    parse_data_points_text,
)
from .config import pipeline_config_from_data
from .const import (
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
    CONF_REJECT_VALUES,
    CONF_REJECT_VALUES_TEXT,
    CONF_STATE_CLASS,
    CONF_WINDOW_DURATION,
    CONF_WINDOW_OUTPUT,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    DOMAIN,
    MAX_DEGREE,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import PipelineConfigurationError, parse_number_list

_BEHAVIOR_ORDER = (
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_WINDOW,
    CONF_ENABLE_ROUNDING,
)
_BEHAVIOR_STEPS = {
    CONF_ENABLE_LIMITS: "value_limits",
    CONF_ENABLE_CALIBRATION: "calibration",
    CONF_ENABLE_WINDOW: "window",
    CONF_ENABLE_ROUNDING: "rounding",
}

_METADATA_KEYS = (
    CONF_UNIT_OF_MEASUREMENT,
    CONF_DEVICE_CLASS,
    CONF_STATE_CLASS,
    CONF_ICON,
)


def _duration_number(value: object) -> float:
    """Convert a persisted duration component to a float."""
    if value is None:
        return 0.0
    if isinstance(value, (str, int, float)):
        return float(value)
    raise ValueError(f"Invalid duration value: {value!r}")


def _duration_default(value: object) -> dict[str, float]:
    """Return a duration-selector-compatible default."""
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


def _behavior_defaults(defaults: Mapping[str, Any]) -> dict[str, bool]:
    """Infer enabled behavior toggles from persisted configuration."""
    inferred = {
        CONF_ENABLE_LIMITS: any(
            key in defaults for key in (CONF_MINIMUM, CONF_MAXIMUM, CONF_REJECT_VALUES)
        ),
        CONF_ENABLE_CALIBRATION: bool(defaults.get(CONF_DATA_POINTS)),
        CONF_ENABLE_WINDOW: float(defaults.get(CONF_WINDOW_DURATION, 0)) > 0,
        CONF_ENABLE_ROUNDING: CONF_PRECISION in defaults,
    }
    return {key: bool(defaults.get(key, value)) for key, value in inferred.items()}


def _source_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    toggles = _behavior_defaults(defaults)
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
            _suggested_optional(CONF_UNIT_OF_MEASUREMENT, defaults): TextSelector(),
            _suggested_optional(CONF_DEVICE_CLASS, defaults): SelectSelector(
                SelectSelectorConfig(
                    options=[device_class.value for device_class in SensorDeviceClass],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            _suggested_optional(CONF_STATE_CLASS, defaults): SelectSelector(
                SelectSelectorConfig(
                    options=[state_class.value for state_class in SensorStateClass],
                    mode=SelectSelectorMode.DROPDOWN,
                )
            ),
            _suggested_optional(CONF_ICON, defaults): IconSelector(),
            vol.Required(
                CONF_ENABLE_LIMITS, default=toggles[CONF_ENABLE_LIMITS]
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_CALIBRATION, default=toggles[CONF_ENABLE_CALIBRATION]
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_WINDOW, default=toggles[CONF_ENABLE_WINDOW]
            ): BooleanSelector(),
            vol.Required(
                CONF_ENABLE_ROUNDING, default=toggles[CONF_ENABLE_ROUNDING]
            ): BooleanSelector(),
        }
    )


def _value_limits_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    rejected = str(defaults.get(CONF_REJECT_VALUES_TEXT, ""))
    if not rejected:
        rejected = ", ".join(
            str(value) for value in defaults.get(CONF_REJECT_VALUES, ())
        )
    number = NumberSelectorConfig(mode=NumberSelectorMode.BOX)
    return vol.Schema(
        {
            _suggested_optional(CONF_MINIMUM, defaults): NumberSelector(number),
            _suggested_optional(CONF_MAXIMUM, defaults): NumberSelector(number),
            vol.Optional(CONF_REJECT_VALUES_TEXT, default=rejected): TextSelector(),
        }
    )


def _calibration_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    points = str(defaults.get(CONF_DATA_POINTS_TEXT, ""))
    if not points:
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
    duration = defaults.get(CONF_WINDOW_DURATION, defaults.get(CONF_DURATION, 60))
    output = str(
        defaults.get(CONF_WINDOW_OUTPUT, defaults.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN))
    )
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
    return vol.Schema(
        {
            vol.Required(
                CONF_PRECISION, default=defaults.get(CONF_PRECISION, DEFAULT_PRECISION)
            ): NumberSelector(
                NumberSelectorConfig(min=0, max=12, step=1, mode=NumberSelectorMode.BOX)
            )
        }
    )


def _normalize_source(
    hass: HomeAssistant, user_input: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    data = dict(user_input)
    enabled = tuple(key for key in _BEHAVIOR_ORDER if bool(data.pop(key, False)))
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
    for key in _METADATA_KEYS:
        value = str(data.get(key, "")).strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)
    return data, enabled


def _normalize_value_limits(user_input: Mapping[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_MINIMUM, CONF_MAXIMUM):
        if data.get(key) in (None, ""):
            data.pop(key, None)
        elif key in data:
            data[key] = float(data[key])
    rejected_text = str(data.pop(CONF_REJECT_VALUES_TEXT, "")).strip()
    rejected = parse_number_list(rejected_text)
    if rejected:
        data[CONF_REJECT_VALUES] = list(rejected)
    return data


def _normalize_calibration(user_input: Mapping[str, Any]) -> dict[str, Any]:
    points_text = str(user_input.get(CONF_DATA_POINTS_TEXT, "")).strip()
    if not points_text:
        raise InvalidDataPointsError("calibration points are required")
    return {
        CONF_DATA_POINTS: [list(pair) for pair in parse_data_points_text(points_text)],
        CONF_DEGREE: int(user_input.get(CONF_DEGREE, DEFAULT_DEGREE)),
    }


def _normalize_window(user_input: Mapping[str, Any]) -> dict[str, Any]:
    seconds = timedelta(**user_input[CONF_DURATION]).total_seconds()
    if seconds <= 0:
        raise PipelineConfigurationError("window duration must be greater than zero")
    return {
        CONF_WINDOW_DURATION: seconds,
        CONF_WINDOW_OUTPUT: str(user_input.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN)),
    }


def _normalize_rounding(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {CONF_PRECISION: int(user_input.get(CONF_PRECISION, DEFAULT_PRECISION))}


class NormifyConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure only the conditioning behaviors explicitly selected by the user."""

    VERSION = 1
    MINOR_VERSION = 0

    def __init__(self) -> None:
        self._flow_data: dict[str, Any] = {}
        self._defaults: Mapping[str, Any] = {}
        self._enabled: tuple[str, ...] = ()
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
        return await self._async_behavior_step(
            "value_limits",
            CONF_ENABLE_LIMITS,
            user_input,
            _value_limits_schema,
            _normalize_value_limits,
        )

    async def async_step_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_behavior_step(
            "calibration",
            CONF_ENABLE_CALIBRATION,
            user_input,
            _calibration_schema,
            _normalize_calibration,
        )

    async def async_step_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_behavior_step(
            "window", CONF_ENABLE_WINDOW, user_input, _window_schema, _normalize_window
        )

    async def async_step_rounding(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_behavior_step(
            "rounding",
            CONF_ENABLE_ROUNDING,
            user_input,
            _rounding_schema,
            _normalize_rounding,
        )

    async def async_step_review(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return await self._finish()
        return self.async_show_form(
            step_id="review",
            data_schema=vol.Schema({}),
            description_placeholders={"pipeline": self._pipeline_summary()},
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
            try:
                self._flow_data, self._enabled = _normalize_source(
                    self.hass, user_input
                )
                self._completed.clear()
                pipeline_config_from_data(self._flow_data)
            except PipelineConfigurationError:
                errors["base"] = "invalid_configuration"
            else:
                return await self._next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=_source_schema(user_input or self._defaults),
            errors=errors,
        )

    async def _async_behavior_step(
        self,
        step_id: str,
        behavior: str,
        user_input: dict[str, Any] | None,
        schema_builder: Any,
        normalizer: Any,
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized = normalizer(user_input)
                candidate = {**self._flow_data, **normalized}
                pipeline_config_from_data(candidate)
            except (InvalidDataPointsError, PipelineConfigurationError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                self._flow_data.update(normalized)
                self._completed.add(behavior)
                return await self._next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=schema_builder(user_input or self._defaults),
            errors=errors,
        )

    async def _next_step(self) -> ConfigFlowResult:
        for behavior in self._enabled:
            if behavior not in self._completed:
                step = cast(
                    Callable[[], Awaitable[ConfigFlowResult]],
                    getattr(self, f"async_step_{_BEHAVIOR_STEPS[behavior]}"),
                )
                return await step()
        return await self.async_step_review()

    async def _finish(self) -> ConfigFlowResult:
        data = dict(self._flow_data)
        pipeline_config_from_data(data)
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

    def _pipeline_summary(self) -> str:
        steps = [str(self._flow_data[CONF_NAME])]
        metadata = [
            f"unit {self._flow_data[CONF_UNIT_OF_MEASUREMENT]}"
            if CONF_UNIT_OF_MEASUREMENT in self._flow_data
            else None,
            f"device class {self._flow_data[CONF_DEVICE_CLASS]}"
            if CONF_DEVICE_CLASS in self._flow_data
            else None,
            f"state class {self._flow_data[CONF_STATE_CLASS]}"
            if CONF_STATE_CLASS in self._flow_data
            else None,
            f"icon {self._flow_data[CONF_ICON]}"
            if CONF_ICON in self._flow_data
            else None,
        ]
        configured_metadata = [item for item in metadata if item is not None]
        if configured_metadata:
            steps.append(f"Override metadata ({', '.join(configured_metadata)})")
        if CONF_ENABLE_LIMITS in self._enabled:
            details: list[str] = []
            if CONF_MINIMUM in self._flow_data:
                details.append(f"minimum {self._flow_data[CONF_MINIMUM]:g}")
            if CONF_MAXIMUM in self._flow_data:
                details.append(f"maximum {self._flow_data[CONF_MAXIMUM]:g}")
            if self._flow_data.get(CONF_REJECT_VALUES):
                details.append("specific rejected values")
            steps.append(
                "Reject bad values" + (f" ({', '.join(details)})" if details else "")
            )
        if CONF_ENABLE_CALIBRATION in self._enabled:
            steps.append(
                f"Calibrate with {len(self._flow_data[CONF_DATA_POINTS])} points"
            )
        if CONF_ENABLE_WINDOW in self._enabled:
            output = self._flow_data[CONF_WINDOW_OUTPUT]
            label = (
                "mean of all readings"
                if output == WINDOW_OUTPUT_MEAN
                else "latest reading"
            )
            steps.append(
                f"Every {self._flow_data[CONF_WINDOW_DURATION]:g} seconds, publish the {label}"
            )
        if CONF_ENABLE_ROUNDING in self._enabled:
            steps.append(f"Round to {self._flow_data[CONF_PRECISION]} decimal places")
        return " → ".join(steps)
