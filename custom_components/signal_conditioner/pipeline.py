"""Pure numeric conditioning pipeline used by Signal Conditioner."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
import math

from .calibration import CalibrationError, PolynomialCalibration

MAX_WINDOW_SECONDS = 86400


class PipelineConfigurationError(ValueError):
    """Raised when a conditioning pipeline is internally inconsistent."""


class Disposition(StrEnum):
    """Outcome of processing or flushing readings."""

    PUBLISH = "publish"
    HOLD = "hold"
    REJECT = "reject"


class WindowOutput(StrEnum):
    """Value published at each configured interval."""

    MEAN = "mean"
    LATEST = "latest"


class LimitMode(StrEnum):
    """What happens to a raw reading outside the configured value limits."""

    REJECT = "reject"
    CLAMP = "clamp"


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Validated configuration for one conditioning pipeline."""

    calibration: PolynomialCalibration | None = None
    minimum: float | None = None
    maximum: float | None = None
    limit_mode: LimitMode = LimitMode.REJECT
    precision: int | None = None
    window_duration: float = 0.0
    window_output: WindowOutput = WindowOutput.MEAN

    def __post_init__(self) -> None:
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise PipelineConfigurationError("minimum cannot exceed maximum")
        if self.precision is not None and self.precision < 0:
            raise PipelineConfigurationError("precision cannot be negative")
        if not 0 <= self.window_duration <= MAX_WINDOW_SECONDS:
            raise PipelineConfigurationError(
                f"window duration must be between 0 and {MAX_WINDOW_SECONDS} seconds"
            )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome returned after processing or closing an interval.

    A HOLD returned by ``process`` still carries the rounded conditioned
    reading so the caller can publish it at once when it has nothing to show
    yet (startup, or recovery after the source was unavailable).
    """

    disposition: Disposition
    value: float | None = None
    reason: str | None = None


class ConditioningPipeline:
    """Condition readings and optionally hold them until the next interval tick.

    A window treats the source as a sample-and-hold signal: each accepted
    reading stays in force until the next one, including across interval
    boundaries. ``mean`` is the time-weighted mean of that signal over the
    interval (like Home Assistant's ``time_simple_moving_average``), so an
    interval with no new readings publishes the value still in force instead
    of leaving a stale average behind.
    """

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._current: float | None = None
        self._current_since = 0.0
        self._area = 0.0
        self._covered = 0.0

    def process(self, source_value: object, timestamp: float = 0.0) -> PipelineResult:  # noqa: PLR0911
        """Validate and condition one source reading received at ``timestamp``."""
        try:
            raw_value = float(source_value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return PipelineResult(
                Disposition.REJECT, reason="source value is not numeric"
            )
        if not math.isfinite(raw_value):
            return PipelineResult(
                Disposition.REJECT, reason="source value must be finite"
            )
        clamp = self.config.limit_mode is LimitMode.CLAMP
        if self.config.minimum is not None and raw_value < self.config.minimum:
            if not clamp:
                return PipelineResult(Disposition.REJECT, reason="below minimum")
            raw_value = self.config.minimum
        if self.config.maximum is not None and raw_value > self.config.maximum:
            if not clamp:
                return PipelineResult(Disposition.REJECT, reason="above maximum")
            raw_value = self.config.maximum

        conditioned = raw_value
        if self.config.calibration is not None:
            try:
                conditioned = self.config.calibration.evaluate(conditioned)
            except CalibrationError as err:
                return PipelineResult(Disposition.REJECT, reason=str(err))

        if self.config.window_duration <= 0:
            return PipelineResult(Disposition.PUBLISH, value=self._round(conditioned))

        self._hold(conditioned, timestamp)
        return PipelineResult(Disposition.HOLD, value=self._round(conditioned))

    def seed(self, value: float, timestamp: float) -> None:
        """Put an already-conditioned value (e.g. restored state) in force."""
        if self.config.window_duration > 0 and math.isfinite(value):
            self._hold(value, timestamp)

    def flush(self, timestamp: float = 0.0) -> PipelineResult:
        """Close the interval ending at ``timestamp`` and publish its value.

        Nothing is published until a reading has been put in force.
        """
        if self._current is None:
            return PipelineResult(Disposition.HOLD)

        self._accumulate(timestamp)
        if self.config.window_output is WindowOutput.MEAN and self._covered > 0:
            selected = self._area / self._covered
        else:
            selected = self._current
        self._area = 0.0
        self._covered = 0.0
        return PipelineResult(Disposition.PUBLISH, value=self._round(selected))

    def reset(self) -> None:
        """Forget the held value, e.g. when the source becomes unavailable."""
        self._current = None
        self._area = 0.0
        self._covered = 0.0

    def _hold(self, value: float, timestamp: float) -> None:
        if self._current is not None:
            self._accumulate(timestamp)
        else:
            self._current_since = timestamp
        self._current = value

    def _accumulate(self, timestamp: float) -> None:
        # Clock steps backwards are treated as zero elapsed time.
        elapsed = max(0.0, timestamp - self._current_since)
        if self._current is not None:
            self._area += self._current * elapsed
            self._covered += elapsed
        self._current_since = max(timestamp, self._current_since)

    def _round(self, value: float) -> float:
        if self.config.precision is None:
            return value
        return round(value, self.config.precision)


def build_pipeline_config(  # noqa: PLR0913
    *,
    data_points: Iterable[Sequence[object]] = (),
    degree: int = 1,
    minimum: float | None = None,
    maximum: float | None = None,
    limit_mode: str = LimitMode.REJECT,
    precision: int | None = None,
    window_duration: float = 0.0,
    window_output: str = WindowOutput.MEAN,
) -> PipelineConfig:
    """Build and validate PipelineConfig from persisted primitive values."""
    points = tuple(tuple(pair) for pair in data_points)
    calibration: PolynomialCalibration | None = None
    if points:
        try:
            calibration = PolynomialCalibration.fit(points, degree=degree)
        except CalibrationError as err:
            raise PipelineConfigurationError(str(err)) from err

    try:
        normalized_window_output = WindowOutput(window_output)
        normalized_limit_mode = LimitMode(limit_mode)
    except ValueError as err:
        raise PipelineConfigurationError(str(err)) from err

    return PipelineConfig(
        calibration=calibration,
        minimum=minimum,
        maximum=maximum,
        limit_mode=normalized_limit_mode,
        precision=precision,
        window_duration=window_duration,
        window_output=normalized_window_output,
    )
