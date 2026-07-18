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
    CONF_REJECT_VALUES,
    CONF_ROUNDING,
    CONF_VALUE_LIMITS,
    CONF_WINDOW,
    CONF_WINDOW_DURATION,
    CONF_WINDOW_OUTPUT,
    DEFAULT_DEGREE,
    DEFAULT_PRECISION,
    WINDOW_OUTPUT_MEAN,
)
from .pipeline import PipelineConfig, build_pipeline_config


def flatten_configuration(data: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten concise YAML behavior blocks into config-entry data."""
    flat = dict(data)

    value_limits = flat.pop(CONF_VALUE_LIMITS, None)
    if isinstance(value_limits, Mapping):
        for key in (CONF_MINIMUM, CONF_MAXIMUM, CONF_REJECT_VALUES):
            if key in value_limits:
                flat[key] = value_limits[key]

    calibration = flat.pop(CONF_CALIBRATION, None)
    if isinstance(calibration, Mapping):
        for key in (CONF_DATA_POINTS, CONF_DEGREE):
            if key in calibration:
                flat[key] = calibration[key]

    window = flat.pop(CONF_WINDOW, None)
    if isinstance(window, Mapping):
        if CONF_DURATION in window:
            flat[CONF_WINDOW_DURATION] = window[CONF_DURATION]
        flat[CONF_WINDOW_OUTPUT] = window.get(CONF_OUTPUT, WINDOW_OUTPUT_MEAN)

    rounding = flat.pop(CONF_ROUNDING, None)
    if isinstance(rounding, Mapping):
        flat[CONF_PRECISION] = rounding.get(CONF_PRECISION, DEFAULT_PRECISION)

    return flat


def pipeline_config_from_data(data: Mapping[str, Any]) -> PipelineConfig:
    """Build the pure pipeline configuration from persisted entry data."""
    flat = flatten_configuration(data)
    return build_pipeline_config(
        data_points=flat.get(CONF_DATA_POINTS, ()),
        degree=int(flat.get(CONF_DEGREE, DEFAULT_DEGREE)),
        minimum=_optional_float(flat, CONF_MINIMUM),
        maximum=_optional_float(flat, CONF_MAXIMUM),
        reject_values=flat.get(CONF_REJECT_VALUES, ()),
        precision=(int(flat[CONF_PRECISION]) if CONF_PRECISION in flat else None),
        window_duration=float(flat.get(CONF_WINDOW_DURATION, 0)),
        window_output=str(flat.get(CONF_WINDOW_OUTPUT, WINDOW_OUTPUT_MEAN)),
    )


def _optional_float(data: Mapping[str, Any], key: str) -> float | None:
    value = data.get(key)
    if value in (None, ""):
        return None
    return float(cast(Any, value))
