"""Keep limits and calibration in the unit they were written in.

Value limits and calibration points are plain numbers in the source's unit at
the time they were configured (the "input unit"). When the source later
reports another unit, its readings are converted to the input unit before
those stages, and the result is converted back so the conditioner keeps
following the source's unit. Units that can't be converted are a
configuration problem: readings are rejected and a Repairs issue explains why.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor.const import UNIT_CONVERTERS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.util.unit_conversion import BaseUnitConverter

from .const import DOMAIN

_ISSUE_PREFIX = "unit_mismatch_"


def unit_converter(
    device_class: str | None, from_unit: str | None, to_unit: str | None
) -> Callable[[float], float] | None:
    """Return a converter between two units of the source's device class.

    The device class picks the converter, as Home Assistant's sensor does:
    some units belong to several converters (°C is both a temperature and a
    temperature difference), so the units alone are ambiguous.
    """
    if from_unit == to_unit:
        return lambda value: value
    converter: type[BaseUnitConverter] | None = UNIT_CONVERTERS.get(device_class)
    if (
        device_class is None
        or converter is None
        or from_unit not in converter.VALID_UNITS
        or to_unit not in converter.VALID_UNITS
    ):
        return None
    return converter.converter_factory(from_unit, to_unit)


def unit_issue_id(entry_id: str) -> str:
    """Return the Repairs issue ID for one conditioner's unit mismatch."""
    return f"{_ISSUE_PREFIX}{entry_id}"


@callback
def async_report_unit_mismatch(  # noqa: PLR0913
    hass: HomeAssistant,
    entry_id: str,
    *,
    name: str,
    source: str,
    source_unit: str | None,
    input_unit: str,
) -> None:
    """Explain that readings are rejected because of an unconvertible unit."""
    async_create_issue(
        hass,
        DOMAIN,
        unit_issue_id(entry_id),
        is_fixable=False,
        is_persistent=False,
        severity=IssueSeverity.ERROR,
        translation_key="unit_mismatch",
        translation_placeholders={
            "conditioner": name,
            "source": source,
            "source_unit": source_unit or "no unit",
            "input_unit": input_unit,
        },
    )


@callback
def async_clear_unit_mismatch(hass: HomeAssistant, entry_id: str) -> None:
    """Remove a unit mismatch issue once readings convert again."""
    async_delete_issue(hass, DOMAIN, unit_issue_id(entry_id))
