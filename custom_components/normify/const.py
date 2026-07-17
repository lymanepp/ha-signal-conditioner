"""Constants for the Normify integration."""

from homeassistant.const import Platform

DOMAIN = "normify"
PLATFORMS = [Platform.SENSOR]

CONF_DATA_POINTS = "data_points"
CONF_DATA_POINTS_TEXT = "data_points_text"
CONF_DEGREE = "degree"
CONF_HIDE_SOURCE = "hide_source"
CONF_PRECISION = "precision"

ATTR_COEFFICIENTS = "coefficients"
ATTR_SOURCE = "source"
ATTR_SOURCE_ATTRIBUTE = "source_attribute"
ATTR_SOURCE_VALUE = "source_value"

DEFAULT_DEGREE = 1
DEFAULT_PRECISION = 2
MAX_DEGREE = 7
