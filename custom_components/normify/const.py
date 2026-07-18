"""Constants for the Normify integration."""

from homeassistant.const import Platform

DOMAIN = "normify"
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
CONF_REJECT_VALUES = "reject_values"
CONF_REJECT_VALUES_TEXT = "reject_values_text"
CONF_PRECISION = "precision"

CONF_VALUE_LIMITS = "value_limits"
CONF_CALIBRATION = "calibration"
CONF_WINDOW = "window"
CONF_ROUNDING = "rounding"
CONF_DURATION = "duration"
CONF_OUTPUT = "output"
CONF_STATE_CLASS = "state_class"

# Flat config-entry keys used by the runtime.
CONF_WINDOW_DURATION = "window_duration"
CONF_WINDOW_OUTPUT = "window_output"

WINDOW_OUTPUT_MEAN = "mean"
WINDOW_OUTPUT_LATEST = "latest"

ATTR_ACCEPTED_SAMPLES = "accepted_samples"
ATTR_COEFFICIENTS = "coefficients"
ATTR_CONDITIONED_VALUE = "conditioned_value"
ATTR_HELD_SAMPLES = "held_samples"
ATTR_LAST_REJECTION = "last_rejection"
ATTR_PUBLISHED_SAMPLES = "published_samples"
ATTR_REJECTED_SAMPLES = "rejected_samples"
ATTR_SOURCE = "source"
ATTR_SOURCE_ATTRIBUTE = "source_attribute"
ATTR_SOURCE_VALUE = "source_value"

DEFAULT_DEGREE = 1
DEFAULT_PRECISION = 2
MAX_DEGREE = 7
MAX_WINDOW_SECONDS = 86400
