"""Pure calibration logic used by Normify.

This module deliberately has no Home Assistant dependencies.  It can be tested
and evolved independently from entity lifecycle and config-flow code.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from numpy.polynomial import Polynomial


class CalibrationError(ValueError):
    """Base exception for invalid calibration input or output."""


class InvalidDataPointsError(CalibrationError):
    """Raised when calibration data points cannot define the requested fit."""


class InvalidSourceValueError(CalibrationError):
    """Raised when a source value cannot be converted to a finite number."""


def _finite_float(value: object, *, description: str) -> float:
    """Convert a value to a finite float."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise InvalidSourceValueError(f"{description} is not numeric") from err

    if not math.isfinite(result):
        raise InvalidSourceValueError(f"{description} must be finite")

    return result


def normalize_data_points(
    data_points: Iterable[Sequence[object]],
) -> tuple[tuple[float, float], ...]:
    """Validate and normalize calibration point pairs."""
    normalized: list[tuple[float, float]] = []

    for index, pair in enumerate(data_points, start=1):
        if len(pair) != 2:
            raise InvalidDataPointsError(
                f"data point {index} must contain exactly two values"
            )

        try:
            x_value = _finite_float(pair[0], description=f"data point {index} input")
            y_value = _finite_float(pair[1], description=f"data point {index} output")
        except InvalidSourceValueError as err:
            raise InvalidDataPointsError(str(err)) from err

        normalized.append((x_value, y_value))

    if not normalized:
        raise InvalidDataPointsError("at least two data points are required")

    return tuple(normalized)


def parse_data_points_text(value: str) -> tuple[tuple[float, float], ...]:
    """Parse one `input, output` calibration pair per line."""
    pairs: list[tuple[str, str]] = []

    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:
            raise InvalidDataPointsError(
                f"line {line_number} must use the format: input, output"
            )
        pairs.append((parts[0], parts[1]))

    return normalize_data_points(pairs)


def format_data_points_text(data_points: Iterable[Sequence[object]]) -> str:
    """Format calibration point pairs for the config-flow text field."""
    return "\n".join(f"{pair[0]}, {pair[1]}" for pair in data_points)


@dataclass(frozen=True, slots=True)
class PolynomialCalibration:
    """A fitted polynomial calibration that transforms raw numeric values."""

    degree: int
    precision: int
    data_points: tuple[tuple[float, float], ...]
    coefficients: tuple[float, ...]
    _polynomial: Polynomial

    @classmethod
    def fit(
        cls,
        data_points: Iterable[Sequence[object]],
        *,
        degree: int,
        precision: int,
    ) -> PolynomialCalibration:
        """Fit a polynomial calibration to point pairs."""
        if degree < 1:
            raise InvalidDataPointsError("degree must be at least 1")
        if precision < 0:
            raise InvalidDataPointsError("precision cannot be negative")

        normalized = normalize_data_points(data_points)
        minimum_points = degree + 1
        if len(normalized) < minimum_points:
            raise InvalidDataPointsError(
                f"data_points must contain at least {minimum_points} points "
                f"for degree {degree}"
            )

        distinct_inputs = {point[0] for point in normalized}
        if len(distinct_inputs) < minimum_points:
            raise InvalidDataPointsError(
                f"data_points must contain at least {minimum_points} distinct "
                f"input values for degree {degree}"
            )

        x_values, y_values = zip(*normalized, strict=True)

        try:
            # Convert from the numerically stable fitted domain/window form to
            # ordinary power-series coefficients.  The resulting callable and
            # coefficients are deterministic and easy to diagnose/test.
            polynomial = Polynomial.fit(x_values, y_values, degree).convert()
        except (FloatingPointError, ValueError, TypeError) as err:
            raise InvalidDataPointsError(
                "unable to fit calibration polynomial"
            ) from err

        coefficients = tuple(float(value) for value in polynomial.coef)
        if not all(math.isfinite(value) for value in coefficients):
            raise InvalidDataPointsError("calibration coefficients must be finite")

        return cls(
            degree=degree,
            precision=precision,
            data_points=normalized,
            coefficients=coefficients,
            _polynomial=polynomial,
        )

    def apply(self, source_value: object) -> float:
        """Transform one source value and round it to configured precision."""
        numeric_value = _finite_float(source_value, description="source value")
        result = float(self._polynomial(numeric_value))
        if not math.isfinite(result):
            raise InvalidSourceValueError("calibrated value must be finite")
        return round(result, self.precision)
