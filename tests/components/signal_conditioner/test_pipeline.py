"""Unit tests for the pure Signal Conditioner pipeline."""

import pytest

from custom_components.signal_conditioner.calibration import PolynomialCalibration
from custom_components.signal_conditioner.pipeline import (
    MAX_WINDOW_SECONDS,
    ConditioningPipeline,
    Disposition,
    LimitMode,
    PipelineConfig,
    PipelineConfigurationError,
    WindowOutput,
    build_pipeline_config,
)


def test_rejects_out_of_range_values() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(minimum=0, maximum=100))
    assert pipeline.process(-1).reason == "below minimum"
    assert pipeline.process(101).reason == "above maximum"


def test_applies_calibration_before_rounding() -> None:
    calibration = PolynomialCalibration.fit([[0, 0], [10, 20]], degree=1)
    pipeline = ConditioningPipeline(
        PipelineConfig(calibration=calibration, precision=2)
    )
    assert pipeline.process(3.333).value == 6.67


def test_mean_is_time_weighted() -> None:
    """Each reading counts for as long as it was in force."""
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.MEAN)
    )
    assert pipeline.process(1, 0).disposition is Disposition.HOLD
    assert pipeline.process(3, 2).disposition is Disposition.HOLD
    assert pipeline.process(8, 8).disposition is Disposition.HOLD
    result = pipeline.flush(10)
    assert result.disposition is Disposition.PUBLISH
    # 1 for 2 s, 3 for 6 s, 8 for 2 s
    assert result.value == pytest.approx((1 * 2 + 3 * 6 + 8 * 2) / 10)


def test_burst_of_readings_does_not_outweigh_steady_value() -> None:
    """Many quick readings are not over-counted the way a sample mean would."""
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60))
    pipeline.process(70, 0)
    for second in range(50, 60):
        pipeline.process(80, second)
    assert pipeline.flush(60).value == pytest.approx((70 * 50 + 80 * 10) / 60)


def test_value_in_force_carries_into_quiet_intervals() -> None:
    """A quiet interval publishes the value still in force, not a stale mean."""
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60, precision=2))
    pipeline.process(72.0, 0)
    pipeline.process(72.1, 30)
    assert pipeline.flush(60).value == 72.05
    assert pipeline.flush(120).value == 72.1
    assert pipeline.flush(180).value == 72.1


def test_held_reading_is_returned_for_immediate_publication() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60, precision=1))
    result = pipeline.process(12.34, 0)
    assert result.disposition is Disposition.HOLD
    assert result.value == 12.3


def test_reset_forgets_the_value_in_force() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60))
    pipeline.process(5, 0)
    pipeline.reset()
    assert pipeline.flush(60).disposition is Disposition.HOLD
    pipeline.process(7, 90)
    assert pipeline.flush(120).value == 7


def test_seed_puts_a_restored_value_in_force() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60))
    pipeline.seed(20, 0)
    pipeline.process(30, 30)
    assert pipeline.flush(60).value == 25


def test_seed_is_ignored_without_a_window() -> None:
    pipeline = ConditioningPipeline(PipelineConfig())
    pipeline.seed(20, 0)
    assert pipeline.flush(60).disposition is Disposition.HOLD


def test_backwards_clock_counts_as_no_elapsed_time() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=60))
    pipeline.process(10, 100)
    pipeline.process(20, 50)
    assert pipeline.flush(160).value == 20


def test_clamp_mode_pins_raw_values_to_the_limits() -> None:
    """Like Home Assistant's range filter, applied before calibration."""
    calibration = PolynomialCalibration.fit([[0, 1], [10, 11]], degree=1)
    pipeline = ConditioningPipeline(
        PipelineConfig(
            calibration=calibration,
            minimum=0,
            maximum=100,
            limit_mode=LimitMode.CLAMP,
        )
    )
    assert pipeline.process(-0.3).value == pytest.approx(1)
    assert pipeline.process(150).value == pytest.approx(101)
    assert pipeline.process(50).value == pytest.approx(51)


def test_invalid_limit_mode_is_rejected() -> None:
    with pytest.raises(PipelineConfigurationError):
        build_pipeline_config(limit_mode="garbage")


def test_latest_interval_uses_same_pipeline_shape() -> None:
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.LATEST)
    )
    pipeline.process(1, 0)
    pipeline.process(3, 1)
    pipeline.process(8, 2)
    assert pipeline.flush(10).value == 8


def test_nothing_is_published_before_the_first_reading() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=10))
    assert pipeline.flush(10).disposition is Disposition.HOLD


def test_rejected_values_never_enter_interval() -> None:
    pipeline = ConditioningPipeline(
        PipelineConfig(minimum=0, maximum=100, window_duration=10)
    )
    pipeline.process(10, 0)
    assert pipeline.process(200, 2).disposition is Disposition.REJECT
    pipeline.process(20, 5)
    assert pipeline.flush(10).value == 15


@pytest.mark.parametrize("bad_value", ["garbage", "nan", "inf", None])
def test_rejects_invalid_source_values(bad_value: object) -> None:
    result = ConditioningPipeline(PipelineConfig()).process(bad_value)
    assert result.disposition is Disposition.REJECT
    assert result.reason is not None


def test_default_pipeline_is_immediate_pass_through() -> None:
    result = ConditioningPipeline(PipelineConfig()).process(12.345)
    assert result.disposition is Disposition.PUBLISH
    assert result.value == 12.345


def test_default_window_output_is_mean() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=10))
    pipeline.process(2, 0)
    pipeline.process(4, 5)
    assert pipeline.flush(10).value == 3


def test_pipeline_config_enforces_maximum_window_duration() -> None:
    with pytest.raises(PipelineConfigurationError, match="between 0"):
        PipelineConfig(window_duration=MAX_WINDOW_SECONDS + 1)
