"""Configuration normalization shared by setup, flows, and entities."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .const import (
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
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import PipelineConfig, build_pipeline_config


def pipeline_config_from_data(data: Mapping[str, Any]) -> PipelineConfig:
    """Build the pure pipeline configuration from canonical nested data."""
    value_limits = _mapping(data.get(CONF_VALUE_LIMITS))
    calibration = _mapping(data.get(CONF_CALIBRATION))
    window = _mapping(data.get(CONF_WINDOW))
    rounding = _mapping(data.get(CONF_ROUNDING))

    return build_pipeline_config(
        data_points=calibration.get(CONF_DATA_POINTS, ()),
        degree=int(calibration.get(CONF_DEGREE, DEFAULT_DEGREE)),
        minimum=_optional_float(value_limits, CONF_MINIMUM),
        maximum=_optional_float(value_limits, CONF_MAXIMUM),
        precision=(
            int(rounding.get(CONF_PRECISION, DEFAULT_PRECISION))
            if CONF_ROUNDING in data
            else None
        ),
        window_duration=float(window.get(CONF_DURATION, 0)),
        window_output=str(window.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN)),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, Any], value)
    return {}


def _optional_float(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    return float(cast(Any, value))
