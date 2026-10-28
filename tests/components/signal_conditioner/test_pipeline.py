"""Unit tests for the pure Signal Conditioner pipeline."""

import pytest

from custom_components.signal_conditioner.calibration import PolynomialCalibration
from custom_components.signal_conditioner.pipeline import (
    MAX_WINDOW_SECONDS,
    ConditioningPipeline,
    Disposition,
    PipelineConfig,
    PipelineConfigurationError,
    WindowOutput,
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


def test_mean_interval_uses_every_accepted_value() -> None:
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.MEAN)
    )
    assert pipeline.process(1).disposition is Disposition.HOLD
    assert pipeline.process(3).disposition is Disposition.HOLD
    assert pipeline.process(8).disposition is Disposition.HOLD
    result = pipeline.flush()
    assert result.disposition is Disposition.PUBLISH
    assert result.value == 4


def test_latest_interval_uses_same_pipeline_shape() -> None:
    pipeline = ConditioningPipeline(
        PipelineConfig(window_duration=10, window_output=WindowOutput.LATEST)
    )
    pipeline.process(1)
    pipeline.process(3)
    pipeline.process(8)
    assert pipeline.flush().value == 8


def test_empty_interval_does_not_publish() -> None:
    pipeline = ConditioningPipeline(PipelineConfig(window_duration=10))
    assert pipeline.flush().disposition is Disposition.HOLD


def test_rejected_values_never_enter_interval() -> None:
    pipeline = ConditioningPipeline(
        PipelineConfig(minimum=0, maximum=100, window_duration=10)
    )
    pipeline.process(10)
    assert pipeline.process(200).disposition is Disposition.REJECT
    pipeline.process(20)
    assert pipeline.flush().value == 15


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
    pipeline.process(2)
    pipeline.process(4)
    assert pipeline.flush().value == 3


def test_pipeline_config_enforces_maximum_window_duration() -> None:
    with pytest.raises(PipelineConfigurationError, match="between 0"):
        PipelineConfig(window_duration=MAX_WINDOW_SECONDS + 1)
