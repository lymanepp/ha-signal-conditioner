"""Unit tests for the pure Signal Conditioner calibration engine."""

from collections.abc import Callable, Sequence
from fractions import Fraction
import random
import warnings

import pytest

from custom_components.signal_conditioner.calibration import (
    CalibrationError,
    PolynomialCalibration,
    format_data_points_text,
    normalize_data_points,
    parse_data_points_text,
)


def test_linear_calibration() -> None:
    """Fit and apply a linear calibration."""
    calibration = PolynomialCalibration.fit([[1, 2], [2, 3]], degree=1)

    assert calibration.evaluate(4) == 5.0
    assert calibration.evaluate(0) == pytest.approx(1.0)


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
    )

    assert calibration.evaluate(43.2) == pytest.approx(3.327, abs=0.001)


def test_requires_degree_plus_one_points() -> None:
    """A polynomial requires at least degree + 1 points."""
    with pytest.raises(CalibrationError, match="at least 3 points"):
        PolynomialCalibration.fit([[1, 2], [2, 3]], degree=2)


def test_requires_degree_plus_one_distinct_inputs() -> None:
    """Duplicate x values cannot independently define a polynomial."""
    with pytest.raises(CalibrationError, match="3 distinct input values"):
        PolynomialCalibration.fit([[1, 2], [1, 3], [2, 4]], degree=2)


def test_data_points_text_round_trip() -> None:
    """Config-flow point text parses and formats predictably."""
    points = parse_data_points_text("# raw, actual\n38.68, 32\n79.89,75\n")

    assert points == ((38.68, 32.0), (79.89, 75.0))
    assert format_data_points_text(points) == "38.68, 32.0\n79.89, 75.0"


@pytest.mark.parametrize(
    "text",
    [
        "1.5, 2.5",
        "1.5 2.5",
        "1.5\t2.5",
        "1,5; 2,5",
        "1,5 2,5",
        "1,5 2.5",
        "1.5;2.5",
    ],
)
def test_data_points_text_separators(text: str) -> None:
    """Comma, whitespace, and (for decimal commas) semicolon separators."""
    assert parse_data_points_text(text) == ((1.5, 2.5),)


def test_empty_data_points_error_says_so() -> None:
    with pytest.raises(CalibrationError, match="no calibration points"):
        parse_data_points_text("# nothing here")


def test_rejects_malformed_data_point_text() -> None:
    """Each line must contain exactly one pair."""
    with pytest.raises(CalibrationError, match="line 2"):
        parse_data_points_text("0, 0\n1, 1, 2")


def test_normalize_rejects_non_finite_points() -> None:
    """Fitted points must be finite."""
    with pytest.raises(CalibrationError, match="must be finite"):
        normalize_data_points([[0, 0], [1, "nan"]])


def _exact_fit(
    points: Sequence[tuple[float, float]], degree: int
) -> Callable[[float], float]:
    """Least-squares fit in exact rational arithmetic: the reference answer."""
    xs = [Fraction(x) for x, _ in points]
    low, high = min(xs), max(xs)

    def mapped(x: Fraction) -> Fraction:
        return (2 * x - low - high) / (high - low)

    t = [mapped(x) for x in xs]
    n = degree + 1
    a = [[sum(ti ** (i + j) for ti in t) for j in range(n)] for i in range(n)]
    b = [
        sum(Fraction(y) * ti**i for ti, (_, y) in zip(t, points, strict=True))
        for i in range(n)
    ]
    for k in range(n):
        pivot = next(row for row in range(k, n) if a[row][k] != 0)
        a[k], a[pivot] = a[pivot], a[k]
        b[k], b[pivot] = b[pivot], b[k]
        for row in range(k + 1, n):
            factor = a[row][k] / a[k][k]
            for col in range(k, n):
                a[row][col] -= factor * a[k][col]
            b[row] -= factor * b[k]
    coefficients = [Fraction(0)] * n
    for i in reversed(range(n)):
        partial = sum(a[i][j] * coefficients[j] for j in range(i + 1, n))
        coefficients[i] = (b[i] - partial) / a[i][i]
    return lambda x: float(
        sum(c * mapped(Fraction(x)) ** k for k, c in enumerate(coefficients))
    )


def _random_case(
    rng: random.Random, *, spread: bool
) -> tuple[list[tuple[float, float]], int]:
    degree = rng.randint(1, 7)
    count = degree + 1 + rng.randint(0, 12)
    low, high = rng.choice(
        [(0, 1), (-50, 150), (60000, 65000), (1e-3, 2e-3), (-1e6, 1e6), (0, 4095)]
    )
    if spread:
        # Inputs spread over the range, as real calibration points are.
        xs = [
            low + (high - low) * (i + rng.uniform(-0.3, 0.3)) / (count - 1)
            for i in range(count)
        ]
    else:
        xs = [rng.uniform(low, high) for _ in range(count)]
    if rng.random() < 0.3:  # repeated samples at the same input
        xs = [rng.choice(xs[: degree + 1]) if rng.random() < 0.4 else x for x in xs]
    true = [rng.uniform(-5, 5) for _ in range(degree + 1)]
    gain = rng.choice([1, 100])
    noise = rng.choice([0, 1e-3, 0.5])
    points = [
        (
            x,
            gain
            * sum(
                c * ((2 * x - low - high) / (high - low)) ** k
                for k, c in enumerate(true)
            )
            + rng.gauss(0, noise),
        )
        for x in xs
    ]
    return points, degree


def _error(
    model: Callable[[float], float],
    exact: Callable[[float], float],
    points: Sequence[tuple[float, float]],
) -> float:
    """Worst deviation from the exact fit, relative to the largest output."""
    xs = [x for x, _ in points]
    low, high = min(xs), max(xs)
    grid = [low + (high - low) * i / 20 for i in range(21)] + xs
    scale = max(abs(y) for _, y in points)
    return max(abs(model(x) - exact(x)) for x in grid) / scale


def test_fit_matches_exact_least_squares() -> None:
    """Realistic calibrations agree with the exact answer to ~1e-9 or better."""
    rng = random.Random(7)
    for _ in range(300):
        points, degree = _random_case(rng, spread=True)
        if len({x for x, _ in points}) <= degree:
            continue
        calibration = PolynomialCalibration.fit(points, degree=degree)
        exact = _exact_fit(points, degree)
        assert _error(calibration.evaluate, exact, points) < 1e-9


def test_fit_is_as_accurate_as_numpy() -> None:
    """Measured against the exact answer, never meaningfully worse than numpy.

    Includes clustered, ill-conditioned degree 6-7 fits. Where the new fit
    is less accurate than numpy, the gap stays below 1e-8 of the output.
    """
    polynomial = pytest.importorskip("numpy.polynomial").Polynomial
    rng = random.Random(99)
    compared = at_least_as_good = 0
    for _ in range(400):
        points, degree = _random_case(rng, spread=False)
        if len({x for x, _ in points}) <= degree:
            continue
        xs, ys = zip(*points, strict=True)
        with warnings.catch_warnings():
            warnings.simplefilter("error")  # skip numpy rank warnings
            try:
                reference = polynomial.fit(xs, ys, degree)
            except Warning:
                continue
        calibration = PolynomialCalibration.fit(points, degree=degree)
        exact = _exact_fit(points, degree)
        ours = _error(calibration.evaluate, exact, points)
        theirs = _error(lambda x, fitted=reference: float(fitted(x)), exact, points)
        assert ours <= theirs + 1e-8
        compared += 1
        at_least_as_good += ours <= theirs
    assert compared > 350
    assert at_least_as_good / compared > 0.7


def test_high_degree_fit_of_large_raw_readings() -> None:
    """ADC-sized inputs at degree 7 keep full precision.

    Plain powers of x (~6e4 ** 7) would lose most digits to cancellation.
    """

    def true(x: float) -> float:
        t = (x - 62500) / 2500
        return 3 + 2 * t - t**2 + 0.5 * t**3 - 0.2 * t**5 + 0.1 * t**7

    points = [(60000 + 5000 * i / 11, true(60000 + 5000 * i / 11)) for i in range(12)]
    calibration = PolynomialCalibration.fit(points, degree=7)
    for x in (60000.0, 61234.5, 62500.0, 64321.0, 65000.0):
        assert calibration.evaluate(x) == pytest.approx(true(x), abs=1e-9)


def test_numerically_coincident_inputs_are_rejected() -> None:
    """Distinct inputs too close to fit independently are a config error.

    Same cutoff as numpy, which starts warning about rank at this 1e-14 gap.
    """
    points = [(float(i), float(i)) for i in range(7)] + [(6 + 1e-14, 6.5)]
    with pytest.raises(CalibrationError, match="too close together"):
        PolynomialCalibration.fit(points, degree=7)
