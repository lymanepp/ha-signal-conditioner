"""Unit tests for the pure Normify conditioning pipeline."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.normify.calibration import PolynomialCalibration
from custom_components.normify.pipeline import (
    ConditioningPipeline,
    Disposition,
    PipelineConfig,
    WindowOutput,
)

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def test_rejects_sentinels_and_out_of_range_values() -> None:
    """Configured garbage and impossible values are rejected."""
    pipeline = ConditioningPipeline(
        PipelineConfig(reject_values=(-999.0,), minimum=0, maximum=100)
    )

    assert pipeline.process(-999, NOW).disposition is Disposition.REJECT
    assert pipeline.process(-1, NOW).reason == "below_minimum"
    assert pipeline.process(101, NOW).reason == "above_maximum"
    assert pipeline.stats.rejected == 3


def test_applies_calibration_before_rounding() -> None:
    """Calibration runs before optional final rounding."""
    calibration = PolynomialCalibration.fit([[0, 0], [10, 20]], degree=1, precision=12)
    pipeline = ConditioningPipeline(
        PipelineConfig(calibration=calibration, precision=2)
    )

    result = pipeline.process(3.333, NOW)

    assert result.value == 6.67


def test_mean_window_collects_every_value_and_publishes_once() -> None:
    """One timer closes the period and publishes the mean of all readings."""
    pipeline = ConditioningPipeline(
        PipelineConfig(
            window_duration=10,
            window_output=WindowOutput.MEAN,
            precision=2,
        )
    )

    first = pipeline.process(1, NOW)
    second = pipeline.process(3, NOW + timedelta(seconds=2))
    third = pipeline.process(8, NOW + timedelta(seconds=9))

    assert first.disposition is Disposition.HOLD
    assert second.disposition is Disposition.HOLD
    assert third.disposition is Disposition.HOLD
    assert first.next_wakeup == NOW + timedelta(seconds=10)
    assert pipeline.flush(NOW + timedelta(seconds=9)).disposition is Disposition.HOLD

    result = pipeline.flush(NOW + timedelta(seconds=10))
    assert result.disposition is Disposition.PUBLISH
    assert result.value == 4.0
    assert pipeline.next_wakeup() is None


def test_latest_window_uses_the_same_timer_and_shape() -> None:
    """Latest differs only in the value selected at the shared boundary."""
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.LATEST)
    )

    pipeline.process(1, NOW)
    pipeline.process(3, NOW + timedelta(seconds=2))
    pipeline.process(8, NOW + timedelta(seconds=9))

    result = pipeline.flush(NOW + timedelta(seconds=10))
    assert result.disposition is Disposition.PUBLISH
    assert result.value == 8


def test_rejected_values_never_enter_the_window() -> None:
    """Value rejection and calibration occur before collection."""
    pipeline = ConditioningPipeline(
        PipelineConfig(
            minimum=0,
            maximum=100,
            window_duration=10,
            window_output=WindowOutput.MEAN,
        )
    )

    pipeline.process(10, NOW)
    assert (
        pipeline.process(200, NOW + timedelta(seconds=2)).disposition
        is Disposition.REJECT
    )
    pipeline.process(20, NOW + timedelta(seconds=4))

    assert pipeline.flush(NOW + timedelta(seconds=10)).value == 15


def test_next_window_starts_with_the_next_accepted_reading() -> None:
    """A completed window is cleared and does not reuse prior readings."""
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.MEAN)
    )

    pipeline.process(10, NOW)
    assert pipeline.flush(NOW + timedelta(seconds=10)).value == 10

    next_reading = pipeline.process(20, NOW + timedelta(seconds=25))
    assert next_reading.next_wakeup == NOW + timedelta(seconds=35)
    assert pipeline.flush(NOW + timedelta(seconds=35)).value == 20


@pytest.mark.parametrize("bad_value", ["garbage", "nan", "inf", None])
def test_rejects_nonfinite_source_values(bad_value: object) -> None:
    """Invalid source values never escape the pure pipeline."""
    pipeline = ConditioningPipeline(PipelineConfig())

    result = pipeline.process(bad_value, NOW)

    assert result.disposition is Disposition.REJECT
    assert result.reason is not None


def test_default_pipeline_is_immediate_unmodified_pass_through() -> None:
    """Omitting every optional behavior still publishes a sane value."""
    pipeline = ConditioningPipeline(PipelineConfig())

    result = pipeline.process(12.345, NOW)

    assert result.disposition is Disposition.PUBLISH
    assert result.value == 12.345


def test_default_window_output_is_mean() -> None:
    """A duration without an explicit output mode selects mean."""
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=10))
    pipeline.process(2, NOW)
    pipeline.process(4, NOW + timedelta(seconds=5))

    assert pipeline.flush(NOW + timedelta(seconds=10)).value == 3
