"""Pure polynomial calibration logic used by Signal Conditioner."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import math

from numpy.polynomial import Polynomial


class CalibrationError(ValueError):
    """Raised when calibration input, fitting, or output is invalid."""


def _finite_float(value: object, *, description: str) -> float:
    """Convert calibration input to a finite float."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise CalibrationError(f"{description} is not numeric") from err
    if not math.isfinite(result):
        raise CalibrationError(f"{description} must be finite")
    return result


def normalize_data_points(
    data_points: Iterable[Sequence[object]],
) -> tuple[tuple[float, float], ...]:
    """Validate and normalize calibration point pairs."""
    normalized: list[tuple[float, float]] = []
    for index, pair in enumerate(data_points, start=1):
        if len(pair) != 2:  # noqa: PLR2004
            raise CalibrationError(
                f"data point {index} must contain exactly two values"
            )
        normalized.append(
            (
                _finite_float(pair[0], description=f"data point {index} input"),
                _finite_float(pair[1], description=f"data point {index} output"),
            )
        )
    if not normalized:
        raise CalibrationError("at least two data points are required")
    return tuple(normalized)


def parse_data_points_text(value: str) -> tuple[tuple[float, float], ...]:
    """Parse one `input, output` calibration pair per line."""
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split(",")]
        if len(parts) != 2:  # noqa: PLR2004
            raise CalibrationError(
                f"line {line_number} must use the format: input, output"
            )
        pairs.append((parts[0], parts[1]))
    return normalize_data_points(pairs)


def format_data_points_text(data_points: Iterable[Sequence[object]]) -> str:
    """Format calibration point pairs for the config-flow text field."""
    return "\n".join(f"{pair[0]}, {pair[1]}" for pair in data_points)


@dataclass(frozen=True, slots=True)
class PolynomialCalibration:
    """A fitted polynomial calibration."""

    degree: int
    data_points: tuple[tuple[float, float], ...]
    coefficients: tuple[float, ...]
    _polynomial: Polynomial

    @classmethod
    def fit(
        cls,
        data_points: Iterable[Sequence[object]],
        *,
        degree: int,
    ) -> PolynomialCalibration:
        """Fit a polynomial calibration to point pairs."""
        if degree < 1:
            raise CalibrationError("degree must be at least 1")
        normalized = normalize_data_points(data_points)
        minimum_points = degree + 1
        if len(normalized) < minimum_points:
            raise CalibrationError(
                f"data_points must contain at least {minimum_points} points "
                f"for degree {degree}"
            )
        if len({point[0] for point in normalized}) < minimum_points:
            raise CalibrationError(
                f"data_points must contain at least {minimum_points} distinct "
                f"input values for degree {degree}"
            )

        x_values, y_values = zip(*normalized, strict=True)
        try:
            polynomial = Polynomial.fit(x_values, y_values, degree).convert()
        except (FloatingPointError, TypeError, ValueError) as err:
            raise CalibrationError("unable to fit calibration polynomial") from err

        coefficients = tuple(float(value) for value in polynomial.coef)
        if not all(math.isfinite(value) for value in coefficients):
            raise CalibrationError("calibration coefficients must be finite")
        return cls(degree, normalized, coefficients, polynomial)

    def evaluate(self, source_value: float) -> float:
        """Transform one already-validated finite source value."""
        result = float(self._polynomial(source_value))
        if not math.isfinite(result):
            raise CalibrationError("calibrated value must be finite")
        return result
