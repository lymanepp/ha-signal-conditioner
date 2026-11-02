"""Sensor platform for Signal Conditioner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
import logging
from typing import Any, cast

from homeassistant.components.sensor import (
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
    async_track_state_change_event,
    async_track_state_report_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .config import availability_from_data, pipeline_config_from_data
from .const import CONF_HIDE_SOURCE, CONF_STATE_CLASS
from .pipeline import ConditioningPipeline, Disposition
from .source_health import SourceReferenceMonitor
from .source_hiding import async_hide_source

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a Signal Conditioner sensor from a config entry."""
    async_add_entities([SignalConditionerSensor(hass, entry)])


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
    * After any gap the first valid reading is published at once.
    """

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the sensor."""
        config = entry.data
        self._source_entity_id = config[CONF_SOURCE]
        self._source_attribute = config.get(CONF_ATTRIBUTE)
        self._hide_source = bool(config.get(CONF_HIDE_SOURCE))
        self._source_hidden = False
        self._pipeline = ConditioningPipeline(pipeline_config_from_data(config))
        self._follow_source, self._stale_after = availability_from_data(config)
        self._cancel_stale: CALLBACK_TYPE | None = None
        self._source_event_generation = 0
        self._source_health = SourceReferenceMonitor(
            hass, entry.entry_id, config[CONF_NAME], self._source_entity_id
        )

        self._attr_unique_id = f"signal_conditioner.{entry.unique_id or entry.entry_id}"
        if entry.source != SOURCE_IMPORT:
            # Show the conditioner on its source's device page.
            self.device_entry = async_entity_id_to_device(hass, self._source_entity_id)
        self._attr_name = config[CONF_NAME]
        # Explicit overrides; anything left unset follows the source.
        self._configured_unit: str | None = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._configured_device_class: str | None = config.get(CONF_DEVICE_CLASS)
        self._configured_state_class: str | None = config.get(CONF_STATE_CLASS)
        self._configured_icon: str | None = config.get(CONF_ICON)
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
            cancel_interval = async_track_time_interval(
                self.hass,
                self._async_interval,
                timedelta(seconds=self._pipeline.config.window_duration),
            )
            self.async_on_remove(cancel_interval)
        self.async_on_remove(self._cancel_stale_timer)

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
        self._pipeline.seed(value, dt_util.utcnow().timestamp())
        self._publish(value, write_state=False)

    @callback
    def _ensure_source_hidden(self) -> None:
        """Hide the source once it is registered (it may register late)."""
        if self._hide_source and not self._source_hidden:
            self._source_hidden = async_hide_source(self.hass, self._source_entity_id)

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
        if self._has_value:
            self._schedule_stale()
        else:
            self._process_source_state(new_state)

    @callback
    def _process_source_state(self, state: State, *, write_state: bool = True) -> None:
        """Extract and condition one source state."""
        if self._source_attribute:
            raw_value = state.attributes.get(self._source_attribute)
            if raw_value is None:
                return
        else:
            raw_value = state.state

        unit_changed = False
        if not self._source_attribute and self._apply_metadata(state.attributes):
            unit_changed = self._has_value
            if unit_changed:
                # Don't average readings taken in different units.
                self._pipeline.reset()

        result = self._pipeline.process(raw_value, dt_util.utcnow().timestamp())
        if result.disposition is Disposition.REJECT:
            _LOGGER.debug(
                "Signal Conditioner rejected %s from %s: %s",
                raw_value,
                self._source_entity_id,
                result.reason,
            )
            if unit_changed:
                # The value showing is in the old unit: don't relabel it.
                self._attr_native_value = None
                if write_state:
                    self.async_write_ha_state()
            return
        if (
            result.disposition is Disposition.PUBLISH
            or not self._has_value
            or unit_changed
        ):
            # A held reading is published at once when nothing is showing,
            # or when the value showing is in a unit the source no longer uses.
            self._publish(result.value, write_state=write_state)
        if self._stale_after is not None and self._has_value:
            self._schedule_stale()

    @callback
    def _async_interval(self, now: datetime) -> None:
        """Publish the value of the interval that just ended."""
        result = self._pipeline.flush(now.timestamp())
        if result.disposition is Disposition.PUBLISH:
            self._publish(result.value)

    @callback
    def _publish(self, value: float | None, *, write_state: bool = True) -> None:
        """Publish one conditioned value."""
        if value is None:
            return
        self._attr_native_value = value
        self._attr_available = True
        if write_state:
            self.async_write_ha_state()

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
        self._attr_state_class = cast(
            SensorStateClass | None,
            self._configured_state_class or attributes.get(ATTR_STATE_CLASS),
        )
        self._attr_icon = self._configured_icon or attributes.get(ATTR_ICON)
        return unit_changed
