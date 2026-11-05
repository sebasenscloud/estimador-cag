from collections.abc import Iterator

import pytest

from app.config import settings
from app.services import llm_service
from app.services.providers import LLMProvider, LLMResult

SYSTEM_PROMPT = "Sos un estimador experto de proyectos de software."
USER_MESSAGE = "<descripcion>App de turnos con login y calendario.</descripcion>"


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self):
        super().__init__(model="fake-model", max_tokens=1000)
        self.generate_calls = []
        self.stream_calls = 0

    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        self.generate_calls.append((system_prompt, user_message))
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


def test_generate_passes_system_and_user_separately(fake_provider):
    llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE)

    assert fake_provider.generate_calls == [(SYSTEM_PROMPT, USER_MESSAGE)]


def test_generate_returns_provider_text(fake_provider):
    assert (
        llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE) == "## Estimación falsa"
    )


def test_generate_uses_cache_for_identical_prompts(fake_provider):
    llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE)
    llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE)

    assert len(fake_provider.generate_calls) == 1


def test_generate_does_not_use_cache_when_a_prompt_changes(fake_provider):
    llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE)
    llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE + " Con pagos online.")
    llm_service.generate_text(SYSTEM_PROMPT + " Versión 2.", USER_MESSAGE)

    assert len(fake_provider.generate_calls) == 3


def test_stream_yields_pieces_and_fills_metrics(fake_provider):
    metrics = {}
    pieces = list(llm_service.stream_text(SYSTEM_PROMPT, USER_MESSAGE, metrics))

    assert pieces == ["## Estimación ", "en streaming"]
    assert metrics["provider"] == "fake"
    assert metrics["output_tokens"] == 90
    assert metrics["cache_hit"] is False
    assert metrics["prompt_version"] == settings.prompt_version


def test_stream_uses_cache_on_second_call(fake_provider):
    list(llm_service.stream_text(SYSTEM_PROMPT, USER_MESSAGE))
    metrics = {}
    pieces = list(llm_service.stream_text(SYSTEM_PROMPT, USER_MESSAGE, metrics))

    assert "".join(pieces) == "## Estimación en streaming"
    assert metrics["cache_hit"] is True
    assert fake_provider.stream_calls == 1


def test_errors_are_not_cached(fake_provider, monkeypatch):
    def failing_generate(system_prompt, user_message):
        raise ValueError("rechazo")

    monkeypatch.setattr(fake_provider, "generate", failing_generate)

    with pytest.raises(ValueError):
        llm_service.generate_text(SYSTEM_PROMPT, USER_MESSAGE)

    assert len(llm_service.cache) == 0
