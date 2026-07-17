"""Sensor platform for Normify."""

from __future__ import annotations

import logging
import math
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
from homeassistant.helpers.event import async_track_state_change_event

from .calibration import InvalidSourceValueError, PolynomialCalibration
from .const import (
    ATTR_COEFFICIENTS,
    ATTR_SOURCE,
    ATTR_SOURCE_ATTRIBUTE,
    ATTR_SOURCE_VALUE,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_HIDE_SOURCE,
    CONF_PRECISION,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a Normify sensor from a config entry."""
    async_add_entities([NormifySensor(hass, entry)])


class NormifySensor(SensorEntity):
    """A calibrated sensor backed by one source state or attribute."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the Normify sensor."""
        config = entry.data
        self._source_entity_id = config[CONF_SOURCE]
        self._source_attribute = config.get(CONF_ATTRIBUTE)
        self._calibration = PolynomialCalibration.fit(
            config[CONF_DATA_POINTS],
            degree=config[CONF_DEGREE],
            precision=config[CONF_PRECISION],
        )
        self._source_value: float | None = None

        self._attr_unique_id = f"normify.{entry.unique_id or entry.entry_id}"
        self._attr_name = config[CONF_NAME]
        self._attr_native_unit_of_measurement = config.get(CONF_UNIT_OF_MEASUREMENT)
        self._attr_device_class = cast(
            SensorDeviceClass | None, config.get(CONF_DEVICE_CLASS)
        )
        self._attr_state_class = cast(
            SensorStateClass | None, config.get("state_class")
        )
        self._attr_icon = None

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
        """Return stable calibration diagnostics."""
        attributes: dict[str, Any] = {
            ATTR_SOURCE: self._source_entity_id,
            ATTR_SOURCE_VALUE: self._source_value,
            ATTR_COEFFICIENTS: list(self._calibration.coefficients),
        }
        if self._source_attribute:
            attributes[ATTR_SOURCE_ATTRIBUTE] = self._source_attribute
        return attributes

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

    @callback
    def _async_source_state_listener(self, event: Event[EventStateChangedData]) -> None:
        """Handle source entity state changes."""
        if (new_state := event.data["new_state"]) is None:
            return
        self._process_source_state(new_state)

    @callback
    def _process_source_state(self, state: State, *, write_state: bool = True) -> None:
        """Extract, calibrate, and publish one source state."""
        if state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return

        if self._source_attribute:
            raw_value = state.attributes.get(self._source_attribute)
            if raw_value is None:
                return
        else:
            raw_value = state.state
            self._inherit_source_metadata(state)

        try:
            source_value = float(raw_value)
            if not math.isfinite(source_value):
                raise InvalidSourceValueError("source value must be finite")
            native_value = self._calibration.apply(source_value)
        except (InvalidSourceValueError, TypeError, ValueError):
            self._source_value = None
            self._attr_native_value = None
            if self._source_attribute:
                _LOGGER.warning(
                    "%s attribute %s is not a finite numeric value",
                    self._source_entity_id,
                    self._source_attribute,
                )
            else:
                _LOGGER.warning(
                    "%s state is not a finite numeric value", self._source_entity_id
                )
        else:
            self._source_value = source_value
            self._attr_native_value = native_value

        if write_state:
            self.async_write_ha_state()

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
