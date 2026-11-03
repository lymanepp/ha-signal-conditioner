"""Config flow for Signal Conditioner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import (
    SOURCE_IMPORT,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    CONF_UNIT_OF_MEASUREMENT,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.selector import (
    AttributeSelector,
    AttributeSelectorConfig,
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
import probatio as vol

from .calibration import (
    CalibrationError,
    PolynomialCalibration,
    format_data_points_text,
    parse_data_points_text,
)
from .config import pipeline_config_from_data
from .const import (
    CONF_AVAILABILITY,
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DATA_POINTS_TEXT,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_ENABLE_CALIBRATION,
    CONF_ENABLE_LIMITS,
    CONF_ENABLE_ROUNDING,
    CONF_ENABLE_WINDOW,
    CONF_FOLLOW_SOURCE,
    CONF_HIDE_SOURCE,
    CONF_INPUT_UNIT,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_MODE,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_ROUNDING,
    CONF_STALE_AFTER,
    CONF_STATE_CLASS,
    CONF_USE_ATTRIBUTE,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DEFAULT_DEGREE,
    DEFAULT_FOLLOW_SOURCE,
    DEFAULT_PRECISION,
    DOMAIN,
    LIMIT_MODE_CLAMP,
    LIMIT_MODE_REJECT,
    MAX_DEGREE,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import MAX_WINDOW_SECONDS, LimitMode, PipelineConfigurationError
from .source_hiding import async_update_hidden_source
from .units import unit_converter

_LOGGER = logging.getLogger(__name__)

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


def _source_schema(
    defaults: Mapping[str, Any], *, exclude_entities: list[str] | None = None
) -> vol.Schema:
    # Any domain: numeric attributes mostly live on climate, weather, fan...
    source_selector = EntitySelector(
        EntitySelectorConfig(exclude_entities=exclude_entities or [])
    )
    fields: dict[vol.Marker, Any] = {
        vol.Required(CONF_SOURCE, default=defaults.get(CONF_SOURCE)): source_selector,
        vol.Optional(
            CONF_USE_ATTRIBUTE,
            default=bool(
                defaults.get(CONF_USE_ATTRIBUTE, bool(defaults.get(CONF_ATTRIBUTE)))
            ),
        ): BooleanSelector(),
    }
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
                CONF_FOLLOW_SOURCE,
                default=bool(
                    defaults.get(
                        CONF_FOLLOW_SOURCE,
                        _section(defaults, CONF_AVAILABILITY).get(
                            CONF_FOLLOW_SOURCE, DEFAULT_FOLLOW_SOURCE
                        ),
                    )
                ),
            ): BooleanSelector(),
            _suggested_optional(
                CONF_STALE_AFTER, _stale_after_defaults(defaults)
            ): DurationSelector(DurationSelectorConfig(enable_day=False)),
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


def _stale_after_defaults(defaults: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stale-after default as a duration mapping, if one is set."""
    if CONF_STALE_AFTER in defaults:
        value = defaults[CONF_STALE_AFTER]
    else:
        value = _section(defaults, CONF_AVAILABILITY).get(CONF_STALE_AFTER)
    if value in (None, "", 0) or value == {}:
        return {}
    return {CONF_STALE_AFTER: _duration_default(value)}


def _value_limits_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    defaults = _section(defaults, CONF_VALUE_LIMITS)
    number = NumberSelectorConfig(mode=NumberSelectorMode.BOX)
    mode = str(defaults.get(CONF_MODE, LIMIT_MODE_REJECT))
    return vol.Schema(
        {
            _suggested_optional(CONF_MINIMUM, defaults): NumberSelector(number),
            _suggested_optional(CONF_MAXIMUM, defaults): NumberSelector(number),
            vol.Required(CONF_MODE, default=mode): SelectSelector(
                SelectSelectorConfig(
                    options=[LIMIT_MODE_REJECT, LIMIT_MODE_CLAMP],
                    mode=SelectSelectorMode.DROPDOWN,
                    translation_key="limit_mode",
                )
            ),
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
            CONF_USE_ATTRIBUTE,
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
        # Distinct from the source, so the two are told apart in the UI and
        # the entity ID isn't the source's with a _2 suffix.
        name = f"{name} (conditioned)"
    data[CONF_NAME] = name

    # The attribute itself is chosen on the next step.
    data.pop(CONF_ATTRIBUTE, None)

    for key in _METADATA_KEYS:
        value = str(data.get(key, "")).strip()
        if value:
            data[key] = value
        else:
            data.pop(key, None)

    availability: dict[str, Any] = {
        CONF_FOLLOW_SOURCE: bool(data.pop(CONF_FOLLOW_SOURCE, DEFAULT_FOLLOW_SOURCE))
    }
    stale_after = data.pop(CONF_STALE_AFTER, None)
    if stale_after not in (None, "", {}):
        seconds = (
            timedelta(**stale_after).total_seconds()
            if isinstance(stale_after, Mapping)
            else float(stale_after)
        )
        if not 0 <= seconds <= MAX_WINDOW_SECONDS:
            raise PipelineConfigurationError("stale_after is out of range")
        if seconds > 0:
            availability[CONF_STALE_AFTER] = seconds
    data[CONF_AVAILABILITY] = availability
    return data, enabled


class SignalConditionerConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a conditioned sensor."""

    VERSION = 1
    MINOR_VERSION = 1

    def __init__(self) -> None:
        self._flow_data: dict[str, Any] = {}
        self._defaults: Mapping[str, Any] = {}
        self._enabled: set[str] = set()
        self._completed: set[str] = set()
        self._reconfigure_entry: ConfigEntry | None = None
        # Unit the limits and calibration are entered in.
        self._input_unit: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_source("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if self._reconfigure_entry is None:
            entry = self._get_reconfigure_entry()
            if entry.source == SOURCE_IMPORT:
                # YAML owns this entry; a UI edit would be overwritten on the
                # next reload or restart.
                return self.async_abort(reason="yaml_managed")
            self._reconfigure_entry = entry
            self._defaults = entry.data
        return await self._async_source("reconfigure", user_input)

    async def async_step_attribute(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the source attribute from those the source has."""
        source = self._flow_data[CONF_SOURCE]
        if user_input is not None:
            attribute = str(user_input.get(CONF_ATTRIBUTE, "")).strip()
            if attribute:
                self._flow_data[CONF_ATTRIBUTE] = attribute
                self._completed.add(CONF_USE_ATTRIBUTE)
                return await self._next_step()
        default = None
        if self._defaults.get(CONF_SOURCE) == source:
            default = self._defaults.get(CONF_ATTRIBUTE)
        key = (
            vol.Required(CONF_ATTRIBUTE, default=default)
            if default
            else vol.Required(CONF_ATTRIBUTE)
        )
        return self.async_show_form(
            step_id="attribute",
            data_schema=vol.Schema(
                {key: AttributeSelector(AttributeSelectorConfig(entity_id=source))}
            ),
            description_placeholders={"source": source},
        )

    async def async_step_value_limits(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                normalized: dict[str, Any] = {
                    key: float(user_input[key])
                    for key in (CONF_MINIMUM, CONF_MAXIMUM)
                    if user_input.get(key) not in (None, "")
                }
                # LimitMode() raises ValueError for anything unsupported.
                normalized[CONF_MODE] = LimitMode(
                    str(user_input.get(CONF_MODE, LIMIT_MODE_REJECT))
                ).value
                minimum = normalized.get(CONF_MINIMUM)
                maximum = normalized.get(CONF_MAXIMUM)
            except (TypeError, ValueError):
                errors["base"] = "invalid_configuration"
            else:
                if minimum is None and maximum is None:
                    errors["base"] = "limits_empty"
                elif minimum is not None and maximum is not None and minimum > maximum:
                    errors["base"] = "minimum_above_maximum"
            if not errors:
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
            description_placeholders=self._unit_placeholder(),
        )

    async def async_step_calibration(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
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
            except CalibrationError as err:
                errors["base"] = "invalid_calibration"
                placeholders["error"] = str(err)
            except (TypeError, ValueError):
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
            description_placeholders={**self._unit_placeholder(), **placeholders},
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
        # Replace, never merge: _abort_if_unique_id_configured(updates=...)
        # merges into the old data, so a block deleted from the YAML (a
        # window, limits, ...) would silently stay in force.
        for entry in self._async_current_entries(include_ignore=False):
            if entry.unique_id != unique_id:
                continue
            if entry.source != SOURCE_IMPORT:
                _LOGGER.error(
                    "Skipping Signal Conditioner YAML key '%s': a UI-created "
                    "configuration already owns this unique ID",
                    unique_id,
                )
                return self.async_abort(reason="already_configured")
            if entry.data != user_input or entry.title != user_input[CONF_NAME]:
                async_update_hidden_source(self.hass, entry, user_input)
                self.hass.config_entries.async_update_entry(
                    entry, title=user_input[CONF_NAME], data=user_input
                )
                # No update listener (reconfigure reloads via
                # async_update_reload_and_abort), so reload here. A not-loaded
                # entry (disabled, or not yet set up at startup) reads the new
                # data when it is set up.
                if entry.state is not ConfigEntryState.NOT_LOADED:
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(title=user_input[CONF_NAME], data=user_input)

    async def _async_source(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input.get(CONF_USE_ATTRIBUTE) and user_input.get(CONF_HIDE_SOURCE):
                # The source entity still matters when one attribute is used.
                errors["base"] = "hide_source_with_attribute"
            else:
                try:
                    self._flow_data, self._enabled = _normalize_source(
                        self.hass, user_input
                    )
                except (TypeError, ValueError):  # incl. PipelineConfigurationError
                    errors["base"] = "invalid_configuration"
                else:
                    self._completed.clear()
                    self._input_unit = self._find_input_unit()
                    return await self._next_step()
        return self.async_show_form(
            step_id=step_id,
            data_schema=_source_schema(
                user_input or self._defaults, exclude_entities=self._own_entities()
            ),
            errors=errors,
        )

    def _own_entities(self) -> list[str]:
        """A conditioner can't be its own source."""
        if self._reconfigure_entry is None:
            return []
        return [
            entity.entity_id
            for entity in er.async_entries_for_config_entry(
                er.async_get(self.hass), self._reconfigure_entry.entry_id
            )
        ]

    def _find_input_unit(self) -> str | None:
        """Return the unit limits and calibration values are entered in.

        An existing conditioner keeps the unit its values were written in;
        a new conditioner, or a new source, uses the source's current unit.
        """
        if CONF_USE_ATTRIBUTE in self._enabled:
            return None  # attributes carry no unit
        source = self._flow_data[CONF_SOURCE]
        state = self.hass.states.get(source)
        attributes = state.attributes if state else {}
        current = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        entry = self._reconfigure_entry
        # A new source's values are entered in its own unit.
        if entry is not None and entry.data[CONF_SOURCE] == source:
            stored = entry.data.get(CONF_INPUT_UNIT) or entry.options.get(
                CONF_INPUT_UNIT
            )
            # Keep it while the source's unit converts to it; otherwise the
            # old values no longer apply and are re-entered in the new unit.
            if stored and (
                current is None
                or unit_converter(attributes.get(ATTR_DEVICE_CLASS), current, stored)
                is not None
            ):
                return str(stored)
        return str(current) if current else None

    def _unit_placeholder(self) -> dict[str, str]:
        return {"unit": self._input_unit or "no unit"}

    async def _next_step(self) -> ConfigFlowResult:
        if (
            CONF_USE_ATTRIBUTE in self._enabled
            and CONF_USE_ATTRIBUTE not in self._completed
        ):
            return await self.async_step_attribute()
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
        if self._input_unit and (CONF_VALUE_LIMITS in data or CONF_CALIBRATION in data):
            data[CONF_INPUT_UNIT] = self._input_unit
        else:
            data.pop(CONF_INPUT_UNIT, None)
        try:
            pipeline_config_from_data(data)
        except PipelineConfigurationError:
            # Each step validates its own part, so this is a combination
            # problem: start over from the first form, keeping the input.
            _LOGGER.exception("Invalid Signal Conditioner configuration")
            return self.async_show_form(
                step_id="reconfigure" if self._reconfigure_entry else "user",
                data_schema=_source_schema(data, exclude_entities=self._own_entities()),
                errors={"base": "invalid_configuration"},
            )
        if entry := self._reconfigure_entry:
            async_update_hidden_source(self.hass, entry, data)
            options = dict(entry.options)  # read after the hidden-source update
            if data[CONF_SOURCE] != entry.data[CONF_SOURCE]:
                # The unit learned from the old source doesn't apply.
                options.pop(CONF_INPUT_UNIT, None)
            return self.async_update_reload_and_abort(
                entry, title=data[CONF_NAME], data=data, options=options
            )
        # No unique ID: one source may have several conditioners.
        return self.async_create_entry(title=data[CONF_NAME], data=data)
