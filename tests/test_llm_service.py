from collections.abc import Iterator

import pytest

from app.services import llm_service
from app.services.providers import LLMProvider, LLMResult

TRANSCRIPTION = "El cliente necesita una app de turnos con login y calendario."


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self):
        super().__init__(model="fake-model", max_tokens=1000)
        self.generate_calls = 0
        self.stream_calls = 0

    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        self.generate_calls += 1
        return LLMResult(
            text="## Estimación falsa",
            provider=self.name,
            model=self.model,
            input_tokens=1200,
            output_tokens=80,
        )

    def stream(
        self, system_prompt: str, user_message: str, usage: dict
    ) -> Iterator[str]:
        self.stream_calls += 1
        yield "## Estimación "
        yield "en streaming"
        usage["model"] = self.model
        usage["input_tokens"] = 1200
        usage["output_tokens"] = 90


@pytest.fixture
def fake_provider(monkeypatch):
    provider = FakeProvider()
    monkeypatch.setattr(llm_service, "get_provider", lambda: provider)
    llm_service.cache.clear()
    yield provider
    llm_service.cache.clear()


def test_system_prompt_includes_instructions_and_examples():
    prompt = llm_service.build_system_prompt()

    assert prompt.startswith("Sos un estimador experto")
    assert '<ejemplo numero="1">' in prompt
    assert "</ejemplos>" in prompt


def test_generate_returns_result_and_misses_cache_first_time(fake_provider):
    result = llm_service.generate_estimation(TRANSCRIPTION)

    assert result["estimation"] == "## Estimación falsa"
    assert result["provider"] == "fake"
    assert result["cache_hit"] is False
    assert fake_provider.generate_calls == 1


def test_generate_uses_cache_for_identical_transcription(fake_provider):
    llm_service.generate_estimation(TRANSCRIPTION)
    second = llm_service.generate_estimation(TRANSCRIPTION)

    assert second["cache_hit"] is True
    assert fake_provider.generate_calls == 1


def test_generate_does_not_use_cache_for_different_transcription(fake_provider):
    llm_service.generate_estimation(TRANSCRIPTION)
    llm_service.generate_estimation(TRANSCRIPTION + " Además quiere pagos online.")

    assert fake_provider.generate_calls == 2


def test_stream_yields_pieces_and_fills_metrics(fake_provider):
    metrics = {}
    pieces = list(llm_service.stream_estimation(TRANSCRIPTION, metrics))

    assert pieces == ["## Estimación ", "en streaming"]
    assert metrics["provider"] == "fake"
    assert metrics["model"] == "fake-model"
    assert metrics["output_tokens"] == 90
    assert metrics["cache_hit"] is False


def test_stream_uses_cache_on_second_call(fake_provider):
    list(llm_service.stream_estimation(TRANSCRIPTION))
    metrics = {}
    pieces = list(llm_service.stream_estimation(TRANSCRIPTION, metrics))

    assert "".join(pieces) == "## Estimación en streaming"
    assert metrics["cache_hit"] is True
    assert fake_provider.stream_calls == 1


def test_errors_are_not_cached(fake_provider, monkeypatch):
    def failing_generate(system_prompt, user_message):
        raise ValueError("rechazo")

    monkeypatch.setattr(fake_provider, "generate", failing_generate)

    with pytest.raises(ValueError):
        llm_service.generate_estimation(TRANSCRIPTION)

    assert len(llm_service.cache) == 0
