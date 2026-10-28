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


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Validated configuration for one conditioning pipeline."""

    calibration: PolynomialCalibration | None = None
    minimum: float | None = None
    maximum: float | None = None
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
    """Outcome returned after processing or closing an interval."""

    disposition: Disposition
    value: float | None = None
    reason: str | None = None


class ConditioningPipeline:
    """Condition readings and optionally hold them until the next interval tick."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self._window_values: list[float] = []

    def process(self, source_value: object) -> PipelineResult:  # noqa: PLR0911
        """Validate and condition one source reading."""
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
        if self.config.minimum is not None and raw_value < self.config.minimum:
            return PipelineResult(Disposition.REJECT, reason="below minimum")
        if self.config.maximum is not None and raw_value > self.config.maximum:
            return PipelineResult(Disposition.REJECT, reason="above maximum")

        conditioned = raw_value
        if self.config.calibration is not None:
            try:
                conditioned = self.config.calibration.evaluate(conditioned)
            except CalibrationError as err:
                return PipelineResult(Disposition.REJECT, reason=str(err))

        if self.config.window_duration <= 0:
            return PipelineResult(Disposition.PUBLISH, value=self._round(conditioned))

        self._window_values.append(conditioned)
        return PipelineResult(Disposition.HOLD)

    def flush(self) -> PipelineResult:
        """Publish the populated interval, or do nothing when it was empty."""
        if not self._window_values:
            return PipelineResult(Disposition.HOLD)

        if self.config.window_output is WindowOutput.MEAN:
            selected = sum(self._window_values) / len(self._window_values)
        else:
            selected = self._window_values[-1]
        self._window_values.clear()
        return PipelineResult(Disposition.PUBLISH, value=self._round(selected))

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
    except ValueError as err:
        raise PipelineConfigurationError(str(err)) from err

    return PipelineConfig(
        calibration=calibration,
        minimum=minimum,
        maximum=maximum,
        precision=precision,
        window_duration=window_duration,
        window_output=normalized_window_output,
    )
