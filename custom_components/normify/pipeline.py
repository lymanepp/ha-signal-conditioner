"""Pure measurement-conditioning pipeline used by Normify.

The pipeline intentionally implements only the production requirements exposed by
Normify: numeric validation, configured value rejection, polynomial calibration,
a single fixed time window, optional rounding, and diagnostics.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, cast

from .calibration import (
    InvalidDataPointsError,
    InvalidSourceValueError,
    PolynomialCalibration,
)


class PipelineConfigurationError(ValueError):
    """Raised when a conditioning pipeline is internally inconsistent."""


class Disposition(StrEnum):
    """Outcome of processing or flushing a reading."""

    PUBLISH = "publish"
    HOLD = "hold"
    REJECT = "reject"


class WindowOutput(StrEnum):
    """Value published when a configured time window ends."""

    MEAN = "mean"
    LATEST = "latest"


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """Validated configuration for one Normify pipeline."""

    calibration: PolynomialCalibration | None = None
    reject_values: tuple[float, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    precision: int | None = None
    window_duration: float = 0.0
    window_output: WindowOutput = WindowOutput.MEAN

    def __post_init__(self) -> None:
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise PipelineConfigurationError("minimum cannot exceed maximum")
        if self.precision is not None and self.precision < 0:
            raise PipelineConfigurationError("precision cannot be negative")
        if self.window_duration < 0:
            raise PipelineConfigurationError("window duration cannot be negative")


@dataclass(slots=True)
class PipelineStats:
    """Counters for observable pipeline behavior."""

    received: int = 0
    accepted: int = 0
    rejected: int = 0
    held: int = 0
    published: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def reject(self, reason: str) -> None:
        self.rejected += 1
        self.rejection_reasons[reason] = self.rejection_reasons.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome returned after processing or closing a window."""

    disposition: Disposition
    value: float | None = None
    raw_value: float | None = None
    conditioned_value: float | None = None
    reason: str | None = None
    next_wakeup: datetime | None = None


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Serializable diagnostic state."""

    last_raw_value: float | None
    last_conditioned_value: float | None
    last_published_value: float | None
    window_started_at: datetime | None
    window_sample_count: int
    last_rejection_reason: str | None
    stats: dict[str, Any]


class ConditioningPipeline:
    """Condition numeric readings and optionally publish by fixed time window."""

    def __init__(self, config: PipelineConfig) -> None:
        self.config = config
        self.stats = PipelineStats()
        self._last_raw_value: float | None = None
        self._last_conditioned_value: float | None = None
        self._last_published_value: float | None = None
        self._last_rejection_reason: str | None = None
        self._window_started_at: datetime | None = None
        self._window_values: list[float] = []
        self._window_raw_values: list[float] = []

    @property
    def coefficients(self) -> tuple[float, ...]:
        """Return calibration coefficients, if calibration is configured."""
        if self.config.calibration is None:
            return ()
        return self.config.calibration.coefficients

    @property
    def last_rejection_reason(self) -> str | None:
        """Return the most recent rejection reason."""
        return self._last_rejection_reason

    def process(self, source_value: object, timestamp: datetime) -> PipelineResult:
        """Validate and condition one source reading."""
        self.stats.received += 1
        try:
            raw_value = _finite_float(source_value)
        except InvalidSourceValueError as err:
            return self._reject("invalid_source", reason_text=str(err))

        self._last_raw_value = raw_value

        if raw_value in self.config.reject_values:
            return self._reject("rejected_value", raw_value=raw_value)
        if self.config.minimum is not None and raw_value < self.config.minimum:
            return self._reject("below_minimum", raw_value=raw_value)
        if self.config.maximum is not None and raw_value > self.config.maximum:
            return self._reject("above_maximum", raw_value=raw_value)

        conditioned = raw_value
        if self.config.calibration is not None:
            conditioned = self.config.calibration.evaluate(conditioned)

        self.stats.accepted += 1
        self._last_conditioned_value = conditioned

        if self.config.window_duration <= 0:
            return self._publish(raw_value, self._round(conditioned))

        if self._window_started_at is None:
            self._window_started_at = timestamp

        self._window_values.append(conditioned)
        self._window_raw_values.append(raw_value)
        self.stats.held += 1
        return PipelineResult(
            Disposition.HOLD,
            raw_value=raw_value,
            conditioned_value=conditioned,
            next_wakeup=self.window_deadline(),
        )

    def flush(self, timestamp: datetime) -> PipelineResult:
        """Publish a populated window once its shared period ends."""
        deadline = self.window_deadline()
        if deadline is None or timestamp < deadline or not self._window_values:
            return PipelineResult(Disposition.HOLD, next_wakeup=deadline)

        if self.config.window_output is WindowOutput.MEAN:
            selected = sum(self._window_values) / len(self._window_values)
        else:
            selected = self._window_values[-1]

        raw_value = self._window_raw_values[-1]
        self._window_values.clear()
        self._window_raw_values.clear()
        self._window_started_at = None
        return self._publish(raw_value, self._round(selected))

    def window_deadline(self) -> datetime | None:
        """Return the one shared aggregation/publication boundary."""
        if self._window_started_at is None or self.config.window_duration <= 0:
            return None
        return self._window_started_at + timedelta(seconds=self.config.window_duration)

    def next_wakeup(self, timestamp: datetime | None = None) -> datetime | None:
        """Return the next required timer wakeup."""
        return self.window_deadline()

    def snapshot(self) -> PipelineSnapshot:
        """Return a serializable diagnostic snapshot."""
        return PipelineSnapshot(
            last_raw_value=self._last_raw_value,
            last_conditioned_value=self._last_conditioned_value,
            last_published_value=self._last_published_value,
            window_started_at=self._window_started_at,
            window_sample_count=len(self._window_values),
            last_rejection_reason=self._last_rejection_reason,
            stats={
                "received": self.stats.received,
                "accepted": self.stats.accepted,
                "rejected": self.stats.rejected,
                "held": self.stats.held,
                "published": self.stats.published,
                "rejection_reasons": dict(self.stats.rejection_reasons),
            },
        )

    def _round(self, value: float) -> float:
        if self.config.precision is None:
            return value
        return round(value, self.config.precision)

    def _publish(self, raw_value: float, value: float) -> PipelineResult:
        self.stats.published += 1
        self._last_published_value = value
        return PipelineResult(
            Disposition.PUBLISH,
            value=value,
            raw_value=raw_value,
            conditioned_value=value,
        )

    def _reject(
        self,
        reason: str,
        *,
        raw_value: float | None = None,
        reason_text: str | None = None,
    ) -> PipelineResult:
        self.stats.reject(reason)
        self._last_rejection_reason = reason
        return PipelineResult(
            Disposition.REJECT,
            raw_value=raw_value,
            reason=reason_text or reason,
            next_wakeup=self.window_deadline(),
        )


def _finite_float(value: object) -> float:
    """Convert an arbitrary source value into a finite float."""
    try:
        result = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as err:
        raise InvalidSourceValueError("source value is not numeric") from err
    if not math.isfinite(result):
        raise InvalidSourceValueError("source value must be finite")
    return result


def parse_number_list(value: str | Iterable[object]) -> tuple[float, ...]:
    """Parse comma/newline-separated numbers or an iterable of values."""
    tokens: list[object]
    if isinstance(value, str):
        tokens = [
            token.strip()
            for line in value.splitlines()
            for token in line.split(",")
            if token.strip()
        ]
    else:
        tokens = list(value)

    numbers: list[float] = []
    for token in tokens:
        try:
            number = float(cast(Any, token))
        except (TypeError, ValueError) as err:
            raise PipelineConfigurationError(
                f"reject value {token!r} is not numeric"
            ) from err
        if not math.isfinite(number):
            raise PipelineConfigurationError("reject values must be finite")
        numbers.append(number)
    return tuple(numbers)


def build_pipeline_config(
    *,
    data_points: Iterable[Sequence[object]] = (),
    degree: int = 1,
    reject_values: Iterable[object] = (),
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
            calibration = PolynomialCalibration.fit(points, degree=degree, precision=12)
        except InvalidDataPointsError as err:
            raise PipelineConfigurationError(str(err)) from err

    try:
        normalized_window_output = WindowOutput(window_output)
    except ValueError as err:
        raise PipelineConfigurationError(str(err)) from err

    return PipelineConfig(
        calibration=calibration,
        reject_values=parse_number_list(reject_values),
        minimum=minimum,
        maximum=maximum,
        precision=precision,
        window_duration=window_duration,
        window_output=normalized_window_output,
    )
