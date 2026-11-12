from types import SimpleNamespace

import httpx
import openai
import pytest
from instructor.core.exceptions import InstructorRetryException

from app.config import settings
from app.schemas import EstimationResult
from app.services import llm_wrapper

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
    monkeypatch.setattr(llm_wrapper, "get_client", lambda: client)
    monkeypatch.setattr(settings, "llm_model", "anthropic/claude-haiku-4-5")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")
    return completions


def generate():
    return llm_wrapper.generate_structured(
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


def test_returns_the_validated_result(fake_completions, estimation_result):
    assert generate() == estimation_result


def test_missing_api_key_raises_provider_error(fake_completions, monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", None)

    with pytest.raises(llm_wrapper.LLMProviderError, match="anthropic"):
        generate()


def test_exhausted_retries_raise_invalid_output_error(fake_completions):
    fake_completions.error = InstructorRetryException(
        "total_cost_eur no coincide", n_attempts=3, total_usage=0
    )

    with pytest.raises(llm_wrapper.InvalidLLMOutputError, match="3 intentos"):
        generate()


def test_api_errors_raise_provider_error(fake_completions):
    fake_completions.error = openai.APIConnectionError(
        request=httpx.Request("POST", "https://api.example.com")
    )

    with pytest.raises(llm_wrapper.LLMProviderError):
        generate()


def test_stream_structured_yields_instructor_partials(monkeypatch):
    calls = []

    def create_partial(**kwargs):
        calls.append(kwargs)
        yield "parcial 1"
        yield "parcial 2"

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create_partial=create_partial))
    )
    monkeypatch.setattr(llm_wrapper, "get_client", lambda: client)
    monkeypatch.setattr(settings, "llm_model", "anthropic/claude-haiku-4-5")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")

    pieces = list(
        llm_wrapper.stream_structured(SYSTEM_PROMPT, USER_MESSAGE, EstimationResult)
    )

    assert pieces == ["parcial 1", "parcial 2"]
    assert calls[0]["response_model"] is EstimationResult
    assert calls[0]["messages"][0]["role"] == "system"


class FakeLiteLLMStream:
    """Imita el CustomStreamWrapper de LiteLLM: es iterable síncrono Y asíncrono.

    Esa doble naturaleza es la que confundía a Instructor y hacía que el
    streaming parcial devolviera un generador async.
    """

    def __init__(self, arguments_pieces: list[str]):
        self._chunks = [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    function=SimpleNamespace(arguments=piece)
                                )
                            ],
                        )
                    )
                ]
            )
            for piece in arguments_pieces
        ]

    def __iter__(self):
        return iter(self._chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise AssertionError("El cliente síncrono no debería iterar en modo async")


def test_partial_streaming_through_real_instructor(monkeypatch, estimation_result):
    payload = estimation_result.model_dump_json()
    pieces = [payload[i : i + 40] for i in range(0, len(payload), 40)]

    def fake_litellm_completion(**kwargs):
        assert kwargs["stream"] is True
        return FakeLiteLLMStream(pieces)

    monkeypatch.setattr(llm_wrapper.litellm, "completion", fake_litellm_completion)
    monkeypatch.setattr(settings, "llm_model", "anthropic/claude-haiku-4-5")
    monkeypatch.setattr(settings, "anthropic_api_key", "fake-key")
    llm_wrapper.get_client.cache_clear()

    try:
        partials = list(
            llm_wrapper.stream_structured(SYSTEM_PROMPT, USER_MESSAGE, EstimationResult)
        )
    finally:
        llm_wrapper.get_client.cache_clear()

    assert len(partials) > 1
    final = EstimationResult.model_validate(partials[-1].model_dump(warnings=False))
    assert final == estimation_result
