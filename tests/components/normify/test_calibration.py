"""Unit tests for the pure Normify calibration engine."""

import math

import pytest

from custom_components.normify.calibration import (
    InvalidDataPointsError,
    InvalidSourceValueError,
    PolynomialCalibration,
    format_data_points_text,
    normalize_data_points,
    parse_data_points_text,
)


def test_linear_calibration() -> None:
    """Fit and apply a linear calibration."""
    calibration = PolynomialCalibration.fit([[1, 2], [2, 3]], degree=1, precision=2)

    assert calibration.apply(4) == 5.0
    assert calibration.coefficients == pytest.approx((1.0, 1.0))


def test_quadratic_calibration_with_repeated_samples() -> None:
    """Repeated samples are valid when enough distinct inputs exist."""
    calibration = PolynomialCalibration.fit(
        [
            [50, 3.3],
            [50, 2.8],
            [50, 2.9],
            [70, 2.3],
            [70, 2.6],
            [70, 2.1],
            [80, 2.5],
            [80, 2.9],
            [80, 2.4],
            [90, 3.0],
            [90, 3.1],
            [90, 2.8],
            [100, 3.3],
            [100, 3.5],
            [100, 3.0],
        ],
        degree=2,
        precision=3,
    )

    assert calibration.apply(43.2) == pytest.approx(3.327)


def test_requires_degree_plus_one_points() -> None:
    """A polynomial requires at least degree + 1 points."""
    with pytest.raises(InvalidDataPointsError, match="at least 3 points"):
        PolynomialCalibration.fit([[1, 2], [2, 3]], degree=2, precision=2)


def test_requires_degree_plus_one_distinct_inputs() -> None:
    """Duplicate x values cannot independently define a polynomial."""
    with pytest.raises(InvalidDataPointsError, match="3 distinct input values"):
        PolynomialCalibration.fit([[1, 2], [1, 3], [2, 4]], degree=2, precision=2)


@pytest.mark.parametrize("bad_value", ["nan", "inf", "-inf", math.nan, math.inf])
def test_rejects_non_finite_source_values(bad_value: object) -> None:
    """NaN and infinity must never become an HA sensor state."""
    calibration = PolynomialCalibration.fit([[0, 0], [1, 1]], degree=1, precision=2)

    with pytest.raises(InvalidSourceValueError, match="finite"):
        calibration.apply(bad_value)


def test_rejects_non_numeric_source_value() -> None:
    """Reject non-numeric source input cleanly."""
    calibration = PolynomialCalibration.fit([[0, 0], [1, 1]], degree=1, precision=2)

    with pytest.raises(InvalidSourceValueError, match="not numeric"):
        calibration.apply("garbage")


def test_data_points_text_round_trip() -> None:
    """Config-flow point text parses and formats predictably."""
    points = parse_data_points_text("# raw, actual\n38.68, 32\n79.89,75\n")

    assert points == ((38.68, 32.0), (79.89, 75.0))
    assert format_data_points_text(points) == "38.68, 32.0\n79.89, 75.0"


def test_rejects_malformed_data_point_text() -> None:
    """Each line must contain exactly one pair."""
    with pytest.raises(InvalidDataPointsError, match="line 2"):
        parse_data_points_text("0, 0\n1, 1, 2")


def test_normalize_rejects_non_finite_points() -> None:
    """Fitted points must be finite."""
    with pytest.raises(InvalidDataPointsError, match="must be finite"):
        normalize_data_points([[0, 0], [1, "nan"]])
