"""Sensor platform for Normify."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, cast

from homeassistant.components.sensor import (
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
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
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.entity_registry import RegistryEntryHider
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.util import dt as dt_util

from .config import pipeline_config_from_data
from .const import (
    ATTR_ACCEPTED_SAMPLES,
    ATTR_COEFFICIENTS,
    ATTR_CONDITIONED_VALUE,
    ATTR_HELD_SAMPLES,
    ATTR_LAST_REJECTION,
    ATTR_PUBLISHED_SAMPLES,
    ATTR_REJECTED_SAMPLES,
    ATTR_SOURCE,
    ATTR_SOURCE_ATTRIBUTE,
    ATTR_SOURCE_VALUE,
    CONF_HIDE_SOURCE,
    CONF_STATE_CLASS,
    DOMAIN,
)
from .pipeline import ConditioningPipeline, Disposition

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a Normify sensor from a config entry."""
    sensor = NormifySensor(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = sensor
    async_add_entities([sensor])


class NormifySensor(SensorEntity):
    """One canonical sensor backed by an in-memory conditioning pipeline."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Normify sensor."""
        config = entry.data
        self._entry_id = entry.entry_id
        self._source_entity_id = config[CONF_SOURCE]
        self._source_attribute = config.get(CONF_ATTRIBUTE)
        self._pipeline = ConditioningPipeline(pipeline_config_from_data(config))
        self._source_value: float | None = None
        self._conditioned_value: float | None = None
        self._cancel_timer: Callable[[], None] | None = None

        self._attr_unique_id = f"normify.{entry.unique_id or entry.entry_id}"
        self._attr_name = config[CONF_NAME]
        self._attr_native_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._attr_device_class = cast(
            SensorDeviceClass | None, config.get(CONF_DEVICE_CLASS)
        )
        self._attr_state_class = cast(
            SensorStateClass | None, config.get(CONF_STATE_CLASS)
        )
        self._attr_icon = config.get(CONF_ICON)
        self._attr_available = False

        if config.get(CONF_HIDE_SOURCE):
            registry = er.async_get(hass)
            source_entry = registry.async_get(self._source_entity_id)
            if source_entry is not None and source_entry.hidden_by is None:
                registry.async_update_entity(
                    self._source_entity_id,
                    hidden_by=RegistryEntryHider.INTEGRATION,
                )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return compact pipeline diagnostics with the canonical state."""
        snapshot = self._pipeline.snapshot()
        stats = snapshot.stats
        attributes: dict[str, Any] = {
            ATTR_SOURCE: self._source_entity_id,
            ATTR_SOURCE_VALUE: self._source_value,
            ATTR_CONDITIONED_VALUE: self._conditioned_value,
            ATTR_ACCEPTED_SAMPLES: stats["accepted"],
            ATTR_REJECTED_SAMPLES: stats["rejected"],
            ATTR_HELD_SAMPLES: stats["held"],
            ATTR_PUBLISHED_SAMPLES: stats["published"],
        }
        if self._pipeline.coefficients:
            attributes[ATTR_COEFFICIENTS] = list(self._pipeline.coefficients)
        if self._pipeline.last_rejection_reason:
            attributes[ATTR_LAST_REJECTION] = self._pipeline.last_rejection_reason
        if self._source_attribute:
            attributes[ATTR_SOURCE_ATTRIBUTE] = self._source_attribute
        return attributes

    @property
    def pipeline(self) -> ConditioningPipeline:
        """Expose the pure pipeline for integration diagnostics."""
        return self._pipeline

    async def async_added_to_hass(self) -> None:
        """Prime the sensor and subscribe to source state changes."""
        if (state := self.hass.states.get(self._source_entity_id)) is not None:
            self._process_source_state(state, write_state=False)

        self.async_on_remove(
            async_track_state_change_event(
                self.hass,
                [self._source_entity_id],
                self._async_source_state_listener,
            )
        )
        self._schedule_timer()

    async def async_will_remove_from_hass(self) -> None:
        """Cancel timers and remove runtime diagnostics reference."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None
        self.hass.data.get(DOMAIN, {}).pop(self._entry_id, None)
        await super().async_will_remove_from_hass()

    @callback
    def _async_source_state_listener(self, event: Event[EventStateChangedData]) -> None:
        """Handle source entity state changes."""
        new_state = event.data["new_state"]
        if new_state is None:
            self._handle_source_unavailable()
            return
        self._process_source_state(new_state)

    @callback
    def _process_source_state(self, state: State, *, write_state: bool = True) -> None:
        """Extract and condition one source state."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            self._handle_source_unavailable(write_state=write_state)
            return

        if self._source_attribute:
            raw_value = state.attributes.get(self._source_attribute)
            if raw_value is None:
                self._handle_source_unavailable(write_state=write_state)
                return
        else:
            raw_value = state.state
            self._inherit_source_metadata(state)

        was_available = self.available
        result = self._pipeline.process(raw_value, dt_util.utcnow())
        self._source_value = result.raw_value
        self._conditioned_value = result.conditioned_value

        if result.disposition is Disposition.PUBLISH:
            self._attr_native_value = result.value
            self._attr_available = True
            if write_state:
                self.async_write_ha_state()
        elif result.disposition is Disposition.HOLD:
            self._attr_available = self._attr_native_value is not None
            if write_state and not was_available and self.available:
                self.async_write_ha_state()
        else:
            _LOGGER.debug(
                "Normify rejected %s from %s: %s",
                raw_value,
                self._source_entity_id,
                result.reason,
            )
            if self._attr_native_value is None:
                self._attr_available = False
                if write_state and was_available != self.available:
                    self.async_write_ha_state()

        self._schedule_timer(result.next_wakeup)

    @callback
    def _handle_source_unavailable(self, *, write_state: bool = True) -> None:
        """Retain the last published value when the source is unavailable."""
        was_available = self.available
        if self._attr_native_value is None:
            self._attr_available = False
        if write_state and was_available != self.available:
            self.async_write_ha_state()
        self._schedule_timer()

    @callback
    def _async_timer(self, now: datetime) -> None:
        """Close the active window and publish its selected value."""
        self._cancel_timer = None
        result = self._pipeline.flush(now)

        if result.disposition is Disposition.PUBLISH:
            self._attr_native_value = result.value
            self._conditioned_value = result.conditioned_value
            self._attr_available = True
            self.async_write_ha_state()

        self._schedule_timer(result.next_wakeup)

    @callback
    def _schedule_timer(self, next_wakeup: datetime | None = None) -> None:
        """Schedule the active window's single publication boundary."""
        if self._cancel_timer is not None:
            self._cancel_timer()
            self._cancel_timer = None

        now = dt_util.utcnow()
        deadlines = [
            deadline
            for deadline in (next_wakeup, self._pipeline.next_wakeup(now))
            if deadline is not None
        ]
        if not deadlines:
            return

        deadline = min(deadlines)
        delay = max((deadline - now).total_seconds(), 0)
        self._cancel_timer = async_call_later(self.hass, delay, self._async_timer)

    @callback
    def _inherit_source_metadata(self, state: State) -> None:
        """Inherit source metadata only when not explicitly configured."""
        if (
            self._attr_native_unit_of_measurement is None
            and (unit := state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)) is not None
        ):
            self._attr_native_unit_of_measurement = unit

        if (
            self._attr_device_class is None
            and (device_class := state.attributes.get(ATTR_DEVICE_CLASS)) is not None
        ):
            self._attr_device_class = cast(SensorDeviceClass, device_class)

        if (
            self._attr_state_class is None
            and (state_class := state.attributes.get(ATTR_STATE_CLASS)) is not None
        ):
            self._attr_state_class = cast(SensorStateClass, state_class)

        if (
            self._attr_icon is None
            and (icon := state.attributes.get(ATTR_ICON)) is not None
        ):
            self._attr_icon = icon
