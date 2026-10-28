"""Constants for the Signal Conditioner integration."""

from homeassistant.const import Platform

DOMAIN = "signal_conditioner"
PLATFORMS = [Platform.SENSOR]

CONF_DATA_POINTS = "data_points"
CONF_DATA_POINTS_TEXT = "data_points_text"
CONF_DEGREE = "degree"
CONF_HIDE_SOURCE = "hide_source"

CONF_ENABLE_LIMITS = "enable_limits"
CONF_ENABLE_CALIBRATION = "enable_calibration"
CONF_ENABLE_WINDOW = "enable_window"
CONF_ENABLE_ROUNDING = "enable_rounding"

CONF_MINIMUM = "minimum"
CONF_MAXIMUM = "maximum"
CONF_PRECISION = "precision"

CONF_VALUE_LIMITS = "value_limits"
CONF_CALIBRATION = "calibration"
CONF_WINDOW = "window"
CONF_ROUNDING = "rounding"
CONF_DURATION = "duration"
CONF_OUTPUT = "output"
CONF_STATE_CLASS = "state_class"
CONF_MODE = "mode"
CONF_AVAILABILITY = "availability"
CONF_FOLLOW_SOURCE = "follow_source"
CONF_STALE_AFTER = "stale_after"


WINDOW_OUTPUT_MEAN = "mean"
WINDOW_OUTPUT_LATEST = "latest"

LIMIT_MODE_REJECT = "reject"
LIMIT_MODE_CLAMP = "clamp"

DEFAULT_DEGREE = 1
DEFAULT_PRECISION = 2
MAX_DEGREE = 7
DEFAULT_FOLLOW_SOURCE = True
