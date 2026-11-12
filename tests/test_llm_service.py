from types import SimpleNamespace

import httpx
import openai
import pytest
from instructor.core.exceptions import InstructorRetryException

from app.config import settings
from app.schemas import EstimationResult
from app.services import llm_service

SYSTEM_PROMPT = "Sos un estimador experto de proyectos de software."
USER_MESSAGE = "<descripcion>App de turnos con login y calendario.</descripcion>"


class FakeCompletions:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def create_with_completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        usage = SimpleNamespace(prompt_tokens=1200, completion_tokens=300)
        return self.result, SimpleNamespace(usage=usage)


@pytest.fixture
def fake_completions(monkeypatch, estimation_result):
    completions = FakeCompletions(result=estimation_result)
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    monkeypatch.setattr(llm_service, "get_client", lambda: client)
    monkeypatch.setattr(settings, "llm_model", "anthropic/claude-haiku-4-5")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")
    llm_service.cache.clear()
    yield completions
    llm_service.cache.clear()


def generate():
    return llm_service.generate_structured(
        SYSTEM_PROMPT, USER_MESSAGE, EstimationResult
    )


def test_sends_system_and_user_as_separate_messages(fake_completions):
    generate()

    assert fake_completions.calls[0]["messages"] == [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_MESSAGE},
    ]


def test_asks_instructor_for_the_response_model_with_retries(fake_completions):
    generate()
    call = fake_completions.calls[0]

    assert call["response_model"] is EstimationResult
    assert call["max_retries"] == settings.llm_max_retries
    assert call["model"] == "anthropic/claude-haiku-4-5"
    assert call["api_key"] == "fake-key"


def test_returns_validated_result_not_cached(fake_completions, estimation_result):
    result, cached = generate()

    assert result == estimation_result
    assert cached is False


def test_second_identical_call_comes_from_cache(fake_completions):
    generate()
    _, cached = generate()

    assert cached is True
    assert len(fake_completions.calls) == 1


def test_different_prompt_is_not_cached(fake_completions):
    generate()
    llm_service.generate_structured(
        SYSTEM_PROMPT, USER_MESSAGE + " Con pagos.", EstimationResult
    )

    assert len(fake_completions.calls) == 2


def test_missing_api_key_raises_provider_error(fake_completions, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    with pytest.raises(llm_service.LLMProviderError, match="anthropic"):
        generate()


def test_exhausted_retries_raise_invalid_output_error(fake_completions):
    fake_completions.error = InstructorRetryException(
        "total_cost_eur no coincide", n_attempts=3, total_usage=0
    )

    with pytest.raises(llm_service.InvalidLLMOutputError, match="3 intentos"):
        generate()
    assert len(llm_service.cache) == 0


def test_api_errors_raise_provider_error(fake_completions):
    fake_completions.error = openai.APIConnectionError(
        request=httpx.Request("POST", "https://api.example.com")
    )

    with pytest.raises(llm_service.LLMProviderError):
        generate()
