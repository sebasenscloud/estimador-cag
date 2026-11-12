import pytest

from app.config import settings
from app.schemas import (
    DetailLevel,
    EstimationRequest,
    EstimationResult,
    OutputFormat,
    ProjectType,
)
from app.services import estimation
from app.services.llm_wrapper import InvalidLLMOutputError

REQUEST = EstimationRequest(
    description="El cliente necesita una app de turnos con login y calendario.",
    project_type=ProjectType.MOBILE_APP,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)


@pytest.fixture
def llm_calls(monkeypatch, estimation_result):
    calls = []

    def fake_generate_structured(system_prompt, user_message, response_model):
        calls.append((system_prompt, user_message, response_model))
        return estimation_result

    monkeypatch.setattr(estimation, "generate_structured", fake_generate_structured)
    estimation.cache.clear()
    yield calls
    estimation.cache.clear()


def test_first_call_goes_to_the_llm(llm_calls, estimation_result):
    response = estimation.estimate(REQUEST)

    assert response.result == estimation_result
    assert response.cached is False
    assert response.prompt_version == settings.prompt_version
    assert llm_calls[0][2] is EstimationResult


def test_identical_request_comes_from_cache(llm_calls):
    estimation.estimate(REQUEST)
    response = estimation.estimate(REQUEST)

    assert response.cached is True
    assert len(llm_calls) == 1


def test_output_format_does_not_split_the_cache(llm_calls):
    estimation.estimate(REQUEST)
    response = estimation.estimate(
        REQUEST.model_copy(update={"output_format": OutputFormat.NARRATIVE})
    )

    assert response.cached is True
    assert len(llm_calls) == 1


def test_any_other_field_changes_the_cache_key(llm_calls):
    estimation.estimate(REQUEST)
    estimation.estimate(
        REQUEST.model_copy(update={"detail_level": DetailLevel.SUMMARY})
    )
    estimation.estimate(
        REQUEST.model_copy(update={"description": REQUEST.description + " Con pagos."})
    )

    assert len(llm_calls) == 3


def test_output_guardrail_runs_before_caching(llm_calls, monkeypatch):
    monkeypatch.setattr(settings, "scope_max_weeks", 10)

    first = estimation.estimate(REQUEST)
    second = estimation.estimate(REQUEST)

    assert first.result.is_out_of_scope
    assert second.cached is True
    assert second.result.is_out_of_scope


def test_failures_are_not_cached(monkeypatch):
    def failing(system_prompt, user_message, response_model):
        raise InvalidLLMOutputError("la suma no cierra")

    monkeypatch.setattr(estimation, "generate_structured", failing)
    estimation.cache.clear()

    with pytest.raises(InvalidLLMOutputError):
        estimation.estimate(REQUEST)
    assert len(estimation.cache) == 0
