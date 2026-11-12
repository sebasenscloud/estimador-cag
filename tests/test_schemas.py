import pytest
from pydantic import ValidationError

from app.schemas import EstimationResult


def result_data(estimation_result: EstimationResult, **changes) -> dict:
    return {**estimation_result.model_dump(), **changes}


def test_valid_result_is_accepted(estimation_result):
    assert estimation_result.total_duration_weeks == 14
    assert len(estimation_result.phases) == 3


def test_weeks_must_match_sum_of_phases(estimation_result):
    with pytest.raises(ValidationError, match="total_duration_weeks"):
        EstimationResult.model_validate(
            result_data(estimation_result, total_duration_weeks=20)
        )


def test_cost_must_match_sum_of_phases(estimation_result):
    with pytest.raises(ValidationError, match="total_cost_eur"):
        EstimationResult.model_validate(
            result_data(estimation_result, total_cost_eur=99000)
        )


def test_confidence_must_be_a_percentage(estimation_result):
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(
            result_data(estimation_result, confidence_pct=120)
        )


def test_at_least_one_phase_is_required(estimation_result):
    with pytest.raises(ValidationError):
        EstimationResult.model_validate(
            result_data(
                estimation_result,
                phases=[],
                total_duration_weeks=1,
                total_cost_eur=0,
            )
        )


def test_json_schema_lists_phases_before_totals():
    schema = EstimationResult.model_json_schema()
    fields = list(schema["properties"])

    assert fields.index("phases") < fields.index("total_duration_weeks")
    assert fields.index("phases") < fields.index("total_cost_eur")
    assert set(schema["required"]) >= {"summary", "phases", "total_cost_eur"}
    assert schema["properties"]["total_cost_eur"]["minimum"] == 0
