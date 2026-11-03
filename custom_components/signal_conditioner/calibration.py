"""Pure polynomial calibration logic used by Signal Conditioner.

The fit matches numpy's ``Polynomial.fit``: inputs are mapped onto [-1, 1]
before a least-squares fit, which keeps high degrees well conditioned even for
large raw readings (e.g. ADC counts), and evaluation happens in that mapped
variable. The least-squares problem is solved with column-scaled Householder
QR, a backward-stable method, so no numpy dependency is needed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
import math
import sys

# Matches numpy's default lstsq cutoff: len(x) * eps. A fit whose condition
# number exceeds 1 / cutoff is rank-deficient in floating point.
_EPSILON = sys.float_info.epsilon


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
        raise CalibrationError("no calibration points were entered")
    return tuple(normalized)


def _split_pair(line: str) -> list[str] | None:
    """Split one line into its two numbers, or return None.

    Accepts ``1.5, 2.5``, ``1.5 2.5`` and, for decimal commas, ``1,5; 2,5``
    or ``1,5 2,5``.
    """
    comma_parts = line.split(",")
    if ";" in line:
        parts, decimal_comma = line.split(";"), True
    elif len(comma_parts) == 2 and not any(  # noqa: PLR2004
        len(part.split()) > 1 for part in comma_parts
    ):
        parts, decimal_comma = comma_parts, False
    else:
        parts, decimal_comma = line.split(), True
    if len(parts) != 2:  # noqa: PLR2004
        return None
    parts = [part.strip() for part in parts]
    if decimal_comma:
        parts = [part.replace(",", ".") for part in parts]
    return parts


def parse_data_points_text(value: str) -> tuple[tuple[float, float], ...]:
    """Parse one `input, output` calibration pair per line."""
    pairs: list[tuple[str, str]] = []
    for line_number, raw_line in enumerate(value.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = _split_pair(line)
        if parts is None:
            raise CalibrationError(
                f"line {line_number} must be two numbers: input, output "
                "(with decimal commas: input; output)"
            )
        pairs.append((parts[0], parts[1]))
    return normalize_data_points(pairs)


def format_data_points_text(data_points: Iterable[Sequence[object]]) -> str:
    """Format calibration point pairs for the config-flow text field."""
    return "\n".join(f"{pair[0]}, {pair[1]}" for pair in data_points)


def _solve_least_squares(
    matrix: list[list[float]], target: list[float]
) -> list[float] | None:
    """Solve min ||matrix @ x - target|| for a full-rank, tall matrix.

    Returns None if the matrix is numerically rank-deficient.
    """
    rows, cols = len(matrix), len(matrix[0])
    # Scale columns to unit norm, as numpy does, so the rank test and the
    # factorization don't depend on how large each power of the input is.
    norms = [math.sqrt(math.fsum(row[j] ** 2 for row in matrix)) for j in range(cols)]
    if not all(norms):
        return None
    scaled = [[row[j] / norms[j] for j in range(cols)] for row in matrix]

    # Householder QR: reduce a copy to upper-triangular R, keeping the
    # reflectors so they can be applied to other right-hand sides.
    a = [list(row) for row in scaled]
    reflectors: list[tuple[int, list[float], float]] = []
    for k in range(cols):
        alpha = math.sqrt(math.fsum(a[i][k] ** 2 for i in range(k, rows)))
        if alpha == 0:
            return None
        if a[k][k] > 0:
            alpha = -alpha
        v = [0.0] * rows
        v[k] = a[k][k] - alpha
        for i in range(k + 1, rows):
            v[i] = a[i][k]
        v_norm2 = math.fsum(v[i] ** 2 for i in range(k, rows))
        reflectors.append((k, v, v_norm2))
        for j in range(k, cols):
            factor = 2 * math.fsum(v[i] * a[i][j] for i in range(k, rows)) / v_norm2
            for i in range(k, rows):
                a[i][j] -= factor * v[i]

    r = [a[i][:cols] for i in range(cols)]
    if _condition_estimate(r) * rows * _EPSILON >= 1:
        return None

    def solve(rhs: list[float]) -> list[float]:
        b = list(rhs)
        for k, v, v_norm2 in reflectors:
            factor = 2 * math.fsum(v[i] * b[i] for i in range(k, rows)) / v_norm2
            for i in range(k, rows):
                b[i] -= factor * v[i]
        x = [0.0] * cols
        for i in reversed(range(cols)):
            partial = math.fsum(r[i][j] * x[j] for j in range(i + 1, cols))
            x[i] = (b[i] - partial) / r[i][i]
        return x

    solution = solve(target)
    # One step of iterative refinement: fit the residual (summed exactly with
    # fsum) and correct. This recovers digits lost to rounding when the fit
    # is ill-conditioned (high degree, clustered inputs).
    residual = [
        target[i] - math.fsum(scaled[i][j] * solution[j] for j in range(cols))
        for i in range(rows)
    ]
    correction = solve(residual)
    solution = [x + d for x, d in zip(solution, correction, strict=True)]
    return [value / norm for value, norm in zip(solution, norms, strict=True)]


def _condition_estimate(r: list[list[float]]) -> float:
    """Return ||R||_F * ||R^-1||_F, an upper bound on cond(R) (within n)."""
    n = len(r)
    if any(r[i][i] == 0 for i in range(n)):
        return math.inf
    inverse_norm2 = 0.0
    for col in range(n):
        # Solve R x = e_col by back-substitution.
        x = [0.0] * n
        for i in reversed(range(n)):
            partial = math.fsum(r[i][j] * x[j] for j in range(i + 1, n))
            x[i] = ((1.0 if i == col else 0.0) - partial) / r[i][i]
        inverse_norm2 += math.fsum(value**2 for value in x)
    r_norm2 = math.fsum(r[i][j] ** 2 for i in range(n) for j in range(i, n))
    return math.sqrt(r_norm2 * inverse_norm2)


def _horner(coefficients: Sequence[float], value: float) -> float:
    result = 0.0
    for coefficient in reversed(coefficients):
        result = result * value + coefficient
    return result


def _standard_coefficients(
    mapped: Sequence[float], offset: float, scale: float
) -> tuple[float, ...]:
    """Expand sum c_k (offset + scale x)^k into plain powers of x."""
    result = [0.0] * len(mapped)
    for k, coefficient in enumerate(mapped):
        for j in range(k + 1):
            result[j] += coefficient * math.comb(k, j) * offset ** (k - j) * scale**j
    return tuple(result)


@dataclass(frozen=True, slots=True)
class PolynomialCalibration:
    """A fitted polynomial calibration.

    ``coefficients`` are in plain powers of the raw input, lowest first, for
    display. Evaluation uses the better-conditioned mapped form instead.
    """

    degree: int
    data_points: tuple[tuple[float, float], ...]
    coefficients: tuple[float, ...]
    _mapped_coefficients: tuple[float, ...]
    _offset: float
    _scale: float

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

        # Map [min, max] of the inputs onto [-1, 1], like numpy's domain.
        x_values = [point[0] for point in normalized]
        low, high = min(x_values), max(x_values)
        scale = 2 / (high - low)
        offset = -(high + low) / (high - low)
        if not (math.isfinite(scale) and math.isfinite(offset)):
            raise CalibrationError("unable to fit calibration polynomial")
        matrix = [
            [(offset + scale * x) ** power for power in range(degree + 1)]
            for x in x_values
        ]
        mapped = _solve_least_squares(matrix, [point[1] for point in normalized])
        if mapped is None:
            raise CalibrationError(
                "data points are too close together to fit a degree "
                f"{degree} polynomial; use fewer degrees or more spread-out inputs"
            )

        coefficients = _standard_coefficients(mapped, offset, scale)
        if not all(math.isfinite(value) for value in (*mapped, *coefficients)):
            raise CalibrationError("calibration coefficients must be finite")
        return cls(degree, normalized, coefficients, tuple(mapped), offset, scale)

    def evaluate(self, source_value: float) -> float:
        """Transform one already-validated finite source value."""
        try:
            result = _horner(
                self._mapped_coefficients, self._offset + self._scale * source_value
            )
        except OverflowError as err:
            raise CalibrationError("calibrated value must be finite") from err
        if not math.isfinite(result):
            raise CalibrationError("calibrated value must be finite")
        return result
