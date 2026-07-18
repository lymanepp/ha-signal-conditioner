"""Tests for Normify setup and YAML validation."""

import pytest
import voluptuous as vol
from homeassistant.const import CONF_SOURCE

from custom_components.normify import CONFIG_SCHEMA
from custom_components.normify.const import (
    CONF_CALIBRATION,
    CONF_DATA_POINTS,
    CONF_DEGREE,
    CONF_DURATION,
    CONF_MAXIMUM,
    CONF_MINIMUM,
    CONF_OUTPUT,
    CONF_PRECISION,
    CONF_ROUNDING,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    CONF_WINDOW_DURATION,
    CONF_WINDOW_OUTPUT,
    DOMAIN,
    WINDOW_OUTPUT_LATEST,
    WINDOW_OUTPUT_MEAN,
)


def test_yaml_mean_window_flattens_to_runtime_shape() -> None:
    """Mean window uses one duration and one publication boundary."""
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "garage_humidity": {
                    CONF_SOURCE: "sensor.garage_humidity_raw",
                    CONF_WINDOW: {
                        CONF_DURATION: 60,
                        CONF_OUTPUT: WINDOW_OUTPUT_MEAN,
                    },
                }
            }
        }
    )
    data = validated[DOMAIN]["garage_humidity"]
    assert data[CONF_WINDOW_DURATION] == 60
    assert data[CONF_WINDOW_OUTPUT] == WINDOW_OUTPUT_MEAN


def test_yaml_latest_window_has_identical_shape() -> None:
    """Throttle-only behavior differs solely by selected output value."""
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "garage_humidity": {
                    CONF_SOURCE: "sensor.garage_humidity_raw",
                    CONF_WINDOW: {
                        CONF_DURATION: 60,
                        CONF_OUTPUT: WINDOW_OUTPUT_LATEST,
                    },
                }
            }
        }
    )
    data = validated[DOMAIN]["garage_humidity"]
    assert data[CONF_WINDOW_DURATION] == 60
    assert data[CONF_WINDOW_OUTPUT] == WINDOW_OUTPUT_LATEST


@pytest.mark.parametrize("output", ["minimum", "maximum", "garbage"])
def test_unsupported_window_outputs_are_rejected(output: str) -> None:
    """The production-focused schema exposes only mean and latest."""
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA(
            {
                DOMAIN: {
                    "garage_humidity": {
                        CONF_SOURCE: "sensor.garage_humidity_raw",
                        CONF_WINDOW: {CONF_DURATION: 60, CONF_OUTPUT: output},
                    }
                }
            }
        )


def test_all_optional_sections_can_be_omitted() -> None:
    """A source-only configuration is a sane numeric pass-through."""
    validated = CONFIG_SCHEMA({DOMAIN: {"plain": {CONF_SOURCE: "sensor.plain_raw"}}})
    data = validated[DOMAIN]["plain"]
    assert data[CONF_SOURCE] == "sensor.plain_raw"
    assert CONF_MINIMUM not in data
    assert CONF_MAXIMUM not in data
    assert CONF_DATA_POINTS not in data
    assert CONF_WINDOW_DURATION not in data
    assert CONF_PRECISION not in data


def test_empty_value_limits_is_a_noop() -> None:
    """An empty limits block performs no filtering."""
    validated = CONFIG_SCHEMA(
        {DOMAIN: {"plain": {CONF_SOURCE: "sensor.plain_raw", CONF_VALUE_LIMITS: {}}}}
    )
    data = validated[DOMAIN]["plain"]
    assert CONF_MINIMUM not in data
    assert CONF_MAXIMUM not in data


@pytest.mark.parametrize(
    ("limits", "present", "absent"),
    [
        ({CONF_MINIMUM: 0}, CONF_MINIMUM, CONF_MAXIMUM),
        ({CONF_MAXIMUM: 100}, CONF_MAXIMUM, CONF_MINIMUM),
    ],
)
def test_each_value_limit_is_independently_optional(
    limits: dict[str, float], present: str, absent: str
) -> None:
    """Minimum and maximum do not require one another."""
    validated = CONFIG_SCHEMA(
        {DOMAIN: {"limited": {CONF_SOURCE: "sensor.raw", CONF_VALUE_LIMITS: limits}}}
    )
    data = validated[DOMAIN]["limited"]
    assert present in data
    assert absent not in data


def test_calibration_requires_data_points() -> None:
    """A calibration section is invalid without calibration points."""
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA(
            {DOMAIN: {"calibrated": {CONF_SOURCE: "sensor.raw", CONF_CALIBRATION: {}}}}
        )


def test_calibration_degree_defaults_to_one() -> None:
    """Omitted calibration degree means an ordinary linear fit."""
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "calibrated": {
                    CONF_SOURCE: "sensor.raw",
                    CONF_CALIBRATION: {CONF_DATA_POINTS: [[0, 0], [10, 20]]},
                }
            }
        }
    )
    assert validated[DOMAIN]["calibrated"][CONF_DEGREE] == 1


def test_window_output_defaults_to_mean() -> None:
    """A window with no output mode publishes its arithmetic mean."""
    validated = CONFIG_SCHEMA(
        {
            DOMAIN: {
                "windowed": {
                    CONF_SOURCE: "sensor.raw",
                    CONF_WINDOW: {CONF_DURATION: 60},
                }
            }
        }
    )
    assert validated[DOMAIN]["windowed"][CONF_WINDOW_OUTPUT] == WINDOW_OUTPUT_MEAN


def test_window_still_requires_duration() -> None:
    """A configured window needs a meaningful time boundary."""
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA(
            {DOMAIN: {"windowed": {CONF_SOURCE: "sensor.raw", CONF_WINDOW: {}}}}
        )


def test_empty_rounding_defaults_to_two_places() -> None:
    """An enabled empty rounding block has a deterministic useful default."""
    validated = CONFIG_SCHEMA(
        {DOMAIN: {"rounded": {CONF_SOURCE: "sensor.raw", CONF_ROUNDING: {}}}}
    )
    assert validated[DOMAIN]["rounded"][CONF_PRECISION] == 2
