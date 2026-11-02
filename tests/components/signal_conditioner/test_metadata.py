"""Unit, device class, state class, and icon follow the source."""

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_DEVICE_CLASS,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIT_OF_MEASUREMENT,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.signal_conditioner.const import (
    CONF_DURATION,
    CONF_MAXIMUM,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    DOMAIN,
)

SOURCE = "sensor.raw"
TARGET = "sensor.conditioned"
FAHRENHEIT = {ATTR_UNIT_OF_MEASUREMENT: "°F", ATTR_DEVICE_CLASS: "temperature"}
CELSIUS = {ATTR_UNIT_OF_MEASUREMENT: "°C", ATTR_DEVICE_CLASS: "temperature"}


async def _setup(hass: HomeAssistant, **data: Any) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Conditioned",
        data={CONF_NAME: "Conditioned", CONF_SOURCE: SOURCE, **data},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _set(hass: HomeAssistant, value: str, attributes: dict) -> None:
    hass.states.async_set(SOURCE, value, attributes)
    await hass.async_block_till_done()


def _attrs(hass: HomeAssistant) -> tuple[str, Any, Any]:
    state = hass.states.get(TARGET)
    assert state is not None
    return (
        state.state,
        state.attributes.get(ATTR_UNIT_OF_MEASUREMENT),
        state.attributes.get(ATTR_DEVICE_CLASS),
    )


async def test_follows_a_source_unit_change(hass: HomeAssistant) -> None:
    await _set(hass, "70", FAHRENHEIT)
    await _setup(hass)
    assert _attrs(hass) == ("70.0", "°F", "temperature")

    await _set(hass, "21", CELSIUS)
    assert _attrs(hass) == ("21.0", "°C", "temperature")


async def test_unit_change_restarts_the_window_and_publishes_at_once(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Readings in different units are never averaged together."""
    await _set(hass, "70", FAHRENHEIT)
    await _setup(hass, **{CONF_WINDOW: {CONF_DURATION: 60}})
    freezer.tick(timedelta(seconds=30))

    await _set(hass, "21", CELSIUS)
    assert _attrs(hass) == ("21.0", "°C", "temperature")

    freezer.tick(timedelta(seconds=30))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _attrs(hass) == ("21.0", "°C", "temperature")


async def test_rejected_reading_after_unit_change_shows_unknown(
    hass: HomeAssistant,
) -> None:
    """The old-unit value must not be relabeled with the new unit."""
    await _set(hass, "70", FAHRENHEIT)
    await _setup(hass, **{CONF_VALUE_LIMITS: {CONF_MAXIMUM: 100}})

    await _set(hass, "300", {**FAHRENHEIT, ATTR_UNIT_OF_MEASUREMENT: "K"})
    assert _attrs(hass) == (STATE_UNKNOWN, "K", "temperature")


async def test_explicit_unit_does_not_inherit_device_class(
    hass: HomeAssistant,
) -> None:
    """E.g. a voltage source calibrated to percent."""
    await _set(
        hass, "3.3", {ATTR_UNIT_OF_MEASUREMENT: "V", ATTR_DEVICE_CLASS: "voltage"}
    )
    await _setup(hass, **{CONF_UNIT_OF_MEASUREMENT: "%"})
    assert _attrs(hass) == ("3.3", "%", None)


async def test_explicit_device_class_still_inherits_unit(hass: HomeAssistant) -> None:
    await _set(hass, "70", {ATTR_UNIT_OF_MEASUREMENT: "°F"})
    await _setup(hass, **{CONF_DEVICE_CLASS: "temperature"})
    assert _attrs(hass) == ("70.0", "°F", "temperature")


async def test_overrides_win_over_source_changes(hass: HomeAssistant) -> None:
    await _set(hass, "70", FAHRENHEIT)
    await _setup(
        hass, **{CONF_UNIT_OF_MEASUREMENT: "°F", CONF_DEVICE_CLASS: "temperature"}
    )
    await _set(hass, "71", CELSIUS)
    assert _attrs(hass) == ("71.0", "°F", "temperature")
