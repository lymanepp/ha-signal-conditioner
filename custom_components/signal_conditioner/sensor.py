"""Sensor platform for Signal Conditioner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
import time
from typing import Any, cast

from homeassistant.components.sensor import (
    ATTR_LAST_RESET,
    ATTR_STATE_CLASS,
    RestoreSensor,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_ICON,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_ATTRIBUTE,
    CONF_DEVICE_CLASS,
    CONF_ICON,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    EventStateReportedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.device import async_entity_id_to_device
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .config import availability_from_data, pipeline_config_from_data
from .const import (
    CONF_CALIBRATION,
    CONF_HIDE_SOURCE,
    CONF_INPUT_UNIT,
    CONF_PRECISION,
    CONF_ROUNDING,
    CONF_STATE_CLASS,
    CONF_VALUE_LIMITS,
)
from .pipeline import (
    ConditioningPipeline,
    Converter,
    Disposition,
    PipelineResult,
    WindowOutput,
)
from .rejections import RejectionMonitor
from .source_health import SourceReferenceMonitor
from .source_hiding import SOURCE_HIDDEN, async_hide_source, async_mark_source_hidden
from .units import async_clear_unit_mismatch, async_report_unit_mismatch, unit_converter
from .windows import next_window_end

_LOGGER = logging.getLogger(__name__)

_COUNTER_CLASSES = (SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a Signal Conditioner sensor from a config entry."""
    sensor = SignalConditionerSensor(hass, entry)
    entry.runtime_data = sensor  # for diagnostics
    async_add_entities([sensor])


class SignalConditionerSensor(RestoreSensor):
    """One sensor backed by a small in-memory conditioning pipeline.

    Availability rules:

    * A first value is published as soon as one exists: the source's current
      reading at setup, else the restored last value. A window never makes
      the sensor wait for its first interval.
    * With ``follow_source`` (default) the sensor is unavailable/unknown while
      its source is, like Home Assistant's filter sensor. Without it, the last
      value is kept, like the original Calibration integration.
    * With ``stale_after`` the sensor becomes unavailable when the source has
      not reported (changed or re-reported) for that long.
    * After any gap, and after a restart while the restored value is
      showing, the first valid reading is published at once.

    Readings are timed with the monotonic clock, so wall-clock steps can't
    distort a window mean; window boundaries follow wall-clock time.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        config = entry.data
        self._entry = entry
        self._source_entity_id = config[CONF_SOURCE]
        self._source_attribute = config.get(CONF_ATTRIBUTE)
        self._hide_source = bool(config.get(CONF_HIDE_SOURCE))
        self._source_hidden = False
        self._pipeline = ConditioningPipeline(pipeline_config_from_data(config))
        self._follow_source, self._stale_after = availability_from_data(config)
        self._cancel_stale: CALLBACK_TYPE | None = None
        self._cancel_window: CALLBACK_TYPE | None = None
        # True while the value showing is the one restored from before a
        # restart rather than a live reading.
        self._showing_restored = False
        self._source_event_generation = 0
        # Limits and calibration are numbers in one unit: the source's unit
        # when they were configured. Explicit, else learned from the source.
        self._unit_sensitive = not self._source_attribute and (
            CONF_VALUE_LIMITS in config or CONF_CALIBRATION in config
        )
        self._input_unit: str | None = config.get(CONF_INPUT_UNIT) or entry.options.get(
            CONF_INPUT_UNIT
        )
        self._unit_issue_raised = False
        self._source_health = SourceReferenceMonitor(
            hass,
            entry.entry_id,
            config[CONF_NAME],
            self._source_entity_id,
            on_confirmed_problem=self._async_source_confirmed_missing,
        )
        self.rejections = RejectionMonitor(
            hass, entry.entry_id, config[CONF_NAME], self._source_entity_id
        )

        self._attr_unique_id = f"signal_conditioner.{entry.unique_id or entry.entry_id}"
        if entry.source != SOURCE_IMPORT:
            # Show the conditioner on its source's device page.
            self.device_entry = async_entity_id_to_device(hass, self._source_entity_id)
        self._attr_name = config[CONF_NAME]
        rounding = config.get(CONF_ROUNDING)
        if isinstance(rounding, Mapping) and rounding.get(CONF_PRECISION) is not None:
            # Show trailing zeros (21.50) and keep converted display units at
            # the same precision.
            self._attr_suggested_display_precision = int(rounding[CONF_PRECISION])
        # Explicit overrides; anything left unset follows the source.
        self._configured_unit: str | None = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._configured_device_class: str | None = config.get(CONF_DEVICE_CLASS)
        self._configured_state_class: str | None = config.get(CONF_STATE_CLASS)
        self._configured_icon: str | None = config.get(CONF_ICON)
        # A calibrated or averaged counter is no longer the meter's reading:
        # calibration can make it decrease, which statistics read as a meter
        # reset, and a mean blends readings from before and after a reset.
        pipeline_config = self._pipeline.config
        self._changes_counter_readings = pipeline_config.calibration is not None or (
            pipeline_config.window_duration > 0
            and pipeline_config.window_output is WindowOutput.MEAN
        )
        # Must be set before _apply_metadata reads it to detect a unit change.
        self._attr_native_unit_of_measurement = None
        self._apply_metadata({})
        self._attr_available = False

    @property
    def _has_value(self) -> bool:
        return self._attr_available and self._attr_native_value is not None

    async def async_added_to_hass(self) -> None:
        """Prime the sensor, subscribe to the source, and start its interval."""
        self._ensure_source_hidden()

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                self._async_source_state_listener,
            )
        )
        self._source_health.async_start(self.async_on_remove)
        self.async_on_remove(self.rejections.async_stop)
        # Subscribe before the initial snapshot: source entities may appear or
        # change while this entity is being added, especially during reloads.
        # Do not await restoration until the source is being observed.
        state = self.hass.states.get(self._source_entity_id)
        if state is not None:
            if state.state == STATE_UNKNOWN and self._follow_source:
                self._attr_available = True
                self._attr_native_value = None
            elif state.state not in (STATE_UNKNOWN, STATE_UNAVAILABLE):
                self._process_source_state(state, write_state=False)
        if not self._has_value and not (
            self._follow_source
            and state is not None
            and state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE)
        ):
            await self._async_restore()

        if self._stale_after is not None:
            # Unchanged re-reports prove the source is alive and end staleness.
            self.async_on_remove(
                async_track_state_report_event(
                    self.hass,
                    [self._source_entity_id],
                    self._async_source_report_listener,
                )
            )
            if self._has_value:
                self._schedule_stale()
        if self._pipeline.config.window_duration > 0:
            self._schedule_window(dt_util.utcnow())
            self.async_on_remove(self._cancel_window_timer)
        self.async_on_remove(self._cancel_stale_timer)
        # Re-raised by the next reading if the mismatch remains.
        self.async_on_remove(
            lambda: async_clear_unit_mismatch(self.hass, self._entry.entry_id)
        )

    async def _async_restore(self) -> None:
        """Show the last value from before the restart until the source reports."""
        generation = self._source_event_generation
        last_data = await self.async_get_last_sensor_data()
        last_state = await self.async_get_last_state()
        # A live source event received while waiting on Recorder must win
        # over an older persisted value (including unknown/unavailable).
        if generation != self._source_event_generation:
            return
        if last_data is None or last_state is None:
            return
        try:
            value = float(cast(float, last_data.native_value))
        except (TypeError, ValueError):
            return
        if not self._source_attribute:
            # Show what was inherited before the restart until the source
            # reports. Use the native unit: the state's unit attribute is the
            # display unit, which may be converted.
            self._apply_metadata(
                {
                    **last_state.attributes,
                    ATTR_UNIT_OF_MEASUREMENT: last_data.native_unit_of_measurement,
                }
            )
        # Not put in force in the window: an hours-old value must not be
        # averaged in as if it were current. It shows until the source
        # reports, and the first live reading replaces it at once.
        self._publish(value, write_state=False)
        self._showing_restored = True

    @callback
    def _ensure_source_hidden(self) -> None:
        """Hide the source once it is registered (it may register late)."""
        if not self._hide_source or self._source_hidden:
            return
        if self._entry.source != SOURCE_IMPORT and self._entry.options.get(
            SOURCE_HIDDEN
        ):
            # Hidden once already: if the user has unhidden it since, that
            # choice stands.
            self._source_hidden = True
            return
        self._source_hidden = async_hide_source(self.hass, self._source_entity_id)
        if self._source_hidden and self._entry.source != SOURCE_IMPORT:
            async_mark_source_hidden(self.hass, self._entry)

    @callback
    def _async_source_state_listener(self, event: Event[EventStateChangedData]) -> None:
        """Handle source entity state changes."""
        self._source_event_generation += 1
        self._source_health.async_source_changed()
        # Sources may be created after the conditioner at startup.
        self._ensure_source_hidden()
        new_state = event.data["new_state"]
        if new_state is None or new_state.state == STATE_UNAVAILABLE:
            if self._follow_source:
                self._go_unavailable()
            return
        if new_state.state == STATE_UNKNOWN:
            if self._follow_source:
                self._go_unknown()
            return
        self._process_source_state(new_state)

    @callback
    def _async_source_report_listener(
        self, event: Event[EventStateReportedData]
    ) -> None:
        """Handle the source re-writing an unchanged state."""
        new_state = event.data["new_state"]
        if new_state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return
        if not self._has_value:
            self._process_source_state(new_state)
            return
        # Only an accepted reading proves the source is alive, as for changes.
        raw_value = self._raw_value(new_state)
        conversion = self._unit_conversion(new_state.attributes)
        if (
            raw_value is not None
            and not isinstance(conversion, str)
            and self._pipeline.condition(raw_value, *conversion).disposition
            is not Disposition.REJECT
        ):
            self._schedule_stale()

    def _raw_value(self, state: State) -> Any:
        """Return the source reading, or None if the attribute is missing."""
        if self._source_attribute:
            return state.attributes.get(self._source_attribute)
        return state.state

    @callback
    def _process_source_state(self, state: State, *, write_state: bool = True) -> None:
        """Extract and condition one source state."""
        raw_value = self._raw_value(state)
        if raw_value is None:
            # The attribute is gone: the reading is unknown, as for a source
            # whose state is unknown.
            if self._follow_source:
                self._go_unknown()
            return

        unit_changed = False
        if not self._source_attribute and self._apply_metadata(state.attributes):
            unit_changed = self._has_value
            if unit_changed:
                # Don't average readings taken in different units.
                self._pipeline.reset()

        conversion = self._unit_conversion(state.attributes)
        if isinstance(conversion, str):
            result = PipelineResult(Disposition.REJECT, reason=conversion)
        else:
            result = self._pipeline.process(raw_value, time.monotonic(), *conversion)
        if result.disposition is Disposition.REJECT:
            _LOGGER.debug(
                "Signal Conditioner rejected %s from %s: %s",
                raw_value,
                self._source_entity_id,
                result.reason,
            )
            self.rejections.record_rejected(
                raw_value,
                str(result.reason),
                reported_elsewhere=isinstance(conversion, str),
            )
            if unit_changed:
                # The value showing is in the old unit: don't relabel it.
                self._attr_native_value = None
                if write_state:
                    self.async_write_ha_state()
            return
        self.rejections.record_accepted()
        if (
            result.disposition is Disposition.PUBLISH
            or not self._has_value
            or self._showing_restored
            or unit_changed
        ):
            # A held reading is published at once when nothing live is
            # showing, or when the value showing is in a unit the source no
            # longer uses.
            self._publish(result.value, write_state=write_state)
        if self._stale_after is not None and self._has_value:
            self._schedule_stale()

    @callback
    def _unit_conversion(
        self, attributes: Mapping[str, Any]
    ) -> tuple[Converter | None, Converter | None] | str:
        """Return (into the input unit, out to the published unit) converters.

        Returns a rejection reason instead when the source's unit can't be
        converted to the unit the limits and calibration were written in.
        """
        if not self._unit_sensitive:
            return None, None
        source_unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        if self._input_unit is None:
            if source_unit is None:
                return None, None
            self._learn_input_unit(source_unit)
        if source_unit == self._input_unit:
            self._clear_unit_issue()
            return None, None
        device_class = attributes.get(ATTR_DEVICE_CLASS)
        convert_in = unit_converter(device_class, source_unit, self._input_unit)
        # With an explicit unit, calibration defines the output unit; else
        # the output follows the source's unit, so convert it back.
        convert_out = (
            None
            if self._configured_unit is not None
            else unit_converter(device_class, self._input_unit, source_unit)
        )
        if convert_in is None or (
            self._configured_unit is None and convert_out is None
        ):
            assert self._input_unit is not None
            if not self._unit_issue_raised:
                self._unit_issue_raised = True
                _LOGGER.warning(
                    "Signal Conditioner '%s' rejects readings from %s: its unit "
                    "%s can't be converted to %s, the unit its limits and "
                    "calibration are in",
                    self._attr_name,
                    self._source_entity_id,
                    source_unit,
                    self._input_unit,
                )
                async_report_unit_mismatch(
                    self.hass,
                    self._entry.entry_id,
                    name=str(self._attr_name),
                    source=self._source_entity_id,
                    source_unit=source_unit,
                    input_unit=self._input_unit,
                )
            return f"unit {source_unit} can't be converted to {self._input_unit}"
        self._clear_unit_issue()
        return convert_in, convert_out

    @callback
    def _learn_input_unit(self, unit: str) -> None:
        """Remember the unit of the first reading for entries without one.

        Entries from before input units were recorded, and YAML entries
        without ``input_unit``, assume the source's unit hasn't changed since
        they were configured. Stored in options: a YAML import replaces data.
        """
        self._input_unit = unit
        self.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, CONF_INPUT_UNIT: unit}
        )

    @callback
    def _clear_unit_issue(self) -> None:
        if self._unit_issue_raised:
            self._unit_issue_raised = False
            async_clear_unit_mismatch(self.hass, self._entry.entry_id)

    @callback
    def _schedule_window(self, after: datetime) -> None:
        """Schedule the next window end on a local clock boundary.

        A 60 s window ends on the minute, a 1-hour window on the hour and a
        1-day window at local midnight; reloads and restarts don't shift them.
        """
        self._cancel_window = async_track_point_in_utc_time(
            self.hass,
            self._async_window_end,
            next_window_end(
                after,
                self._pipeline.config.window_duration,
                dt_util.get_default_time_zone(),
            ),
        )

    @callback
    def _cancel_window_timer(self) -> None:
        if self._cancel_window is not None:
            self._cancel_window()
            self._cancel_window = None

    @callback
    def _async_window_end(self, scheduled: datetime) -> None:
        """Publish the value of the interval that just ended."""
        # Never schedule a boundary that has already passed (e.g. after the
        # host was suspended).
        self._schedule_window(max(scheduled, dt_util.utcnow()))
        # Same clock as the readings, so a late tick can't misorder them.
        result = self._pipeline.flush(time.monotonic())
        if result.disposition is Disposition.PUBLISH:
            self._publish(result.value)

    @callback
    def _publish(self, value: float | None, *, write_state: bool = True) -> None:
        """Publish one conditioned value."""
        if value is None:
            return
        self._showing_restored = False
        self._attr_native_value = value
        self._attr_available = True
        if write_state:
            self.async_write_ha_state()

    @callback
    def _async_source_confirmed_missing(self) -> None:
        """Don't keep showing a value once the source is confirmed gone.

        Matters after a restart: the restored value would otherwise show as
        current indefinitely if the source never comes back.
        """
        if self._follow_source:
            self._go_unavailable()

    @callback
    def _go_unavailable(self) -> None:
        """Mirror an unavailable or stale source."""
        self._pipeline.reset()
        self._cancel_stale_timer()
        if self._attr_available:
            self._attr_available = False
            self.async_write_ha_state()

    @callback
    def _go_unknown(self) -> None:
        """Mirror a source whose state is unknown."""
        self._pipeline.reset()
        self._cancel_stale_timer()
        if not self._attr_available or self._attr_native_value is not None:
            self._attr_available = True
            self._attr_native_value = None
            self.async_write_ha_state()

    @callback
    def _schedule_stale(self) -> None:
        self._cancel_stale_timer()
        assert self._stale_after is not None
        self._cancel_stale = async_call_later(
            self.hass, self._stale_after, self._async_source_stale
        )

    @callback
    def _cancel_stale_timer(self) -> None:
        if self._cancel_stale is not None:
            self._cancel_stale()
            self._cancel_stale = None

    @callback
    def _async_source_stale(self, _now: datetime) -> None:
        self._cancel_stale = None
        _LOGGER.debug(
            "Signal Conditioner source %s has not reported for %s seconds",
            self._source_entity_id,
            self._stale_after,
        )
        self._go_unavailable()

    @callback
    def _apply_metadata(self, attributes: Mapping[str, Any]) -> bool:
        """Set metadata from overrides, else from the source's attributes.

        Re-evaluated on every source reading, so a source that changes its
        unit is followed instead of being labeled with a stale one. The device
        class is inherited only together with the unit: an explicit unit
        (e.g. % for a voltage source) must not be paired with the source's
        device class. Returns True if the unit changed.
        """
        unit = self._configured_unit
        device_class = self._configured_device_class
        if unit is None:
            unit = attributes.get(ATTR_UNIT_OF_MEASUREMENT)
            if device_class is None:
                device_class = attributes.get(ATTR_DEVICE_CLASS)
        unit_changed = unit != self._attr_native_unit_of_measurement
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = cast(SensorDeviceClass | None, device_class)
        state_class = self._configured_state_class
        if state_class is None:
            state_class = attributes.get(ATTR_STATE_CLASS)
            if self._changes_counter_readings and state_class in _COUNTER_CLASSES:
                state_class = None
        self._attr_state_class = cast(SensorStateClass | None, state_class)
        # A total's statistics need its reset time, as the source reports it.
        last_reset = attributes.get(ATTR_LAST_RESET)
        self._attr_last_reset = (
            dt_util.parse_datetime(str(last_reset))
            if state_class == SensorStateClass.TOTAL and last_reset
            else None
        )
        self._attr_icon = self._configured_icon or attributes.get(ATTR_ICON)
        return unit_changed
