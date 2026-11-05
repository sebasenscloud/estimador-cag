from collections.abc import Iterator

import pytest

from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.services import llm_service
from app.services.providers import LLMProvider, LLMResult

REQUEST = EstimationRequest(
    description="El cliente necesita una app de turnos con login y calendario.",
    project_type=ProjectType.MOBILE_APP,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)


class FakeProvider(LLMProvider):
    name = "fake"

    def __init__(self):
        super().__init__(model="fake-model", max_tokens=1000)
        self.generate_calls = 0
        self.stream_calls = 0
        self.last_user_message = None

    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        self.generate_calls += 1
        self.last_user_message = user_message
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


def test_user_message_includes_all_form_fields():
    message = llm_service.build_user_message(REQUEST)

    assert "Aplicación móvil" in message
    assert "Intermedio" in message
    assert "tabla en Markdown" in message
    assert REQUEST.description in message


def test_each_option_has_prompt_instructions():
    assert set(llm_service.DETAIL_LEVEL_INSTRUCTIONS) == set(DetailLevel)
    assert set(llm_service.OUTPUT_FORMAT_INSTRUCTIONS) == set(OutputFormat)


def test_generate_returns_text_and_prompt_version(fake_provider):
    response = llm_service.generate_estimation(REQUEST)

    assert response.text == "## Estimación falsa"
    assert response.prompt_version == llm_service.PROMPT_VERSION
    assert fake_provider.generate_calls == 1


def test_generate_uses_cache_for_identical_request(fake_provider):
    llm_service.generate_estimation(REQUEST)
    llm_service.generate_estimation(REQUEST)

    assert fake_provider.generate_calls == 1


def test_generate_does_not_use_cache_when_any_field_changes(fake_provider):
    llm_service.generate_estimation(REQUEST)
    llm_service.generate_estimation(
        REQUEST.model_copy(update={"output_format": OutputFormat.NARRATIVE})
    )

    assert fake_provider.generate_calls == 2


def test_stream_yields_pieces_and_fills_metrics(fake_provider):
    metrics = {}
    pieces = list(llm_service.stream_estimation(REQUEST, metrics))

    assert pieces == ["## Estimación ", "en streaming"]
    assert metrics["provider"] == "fake"
    assert metrics["output_tokens"] == 90
    assert metrics["cache_hit"] is False
    assert metrics["prompt_version"] == llm_service.PROMPT_VERSION


def test_stream_uses_cache_on_second_call(fake_provider):
    list(llm_service.stream_estimation(REQUEST))
    metrics = {}
    pieces = list(llm_service.stream_estimation(REQUEST, metrics))

    assert "".join(pieces) == "## Estimación en streaming"
    assert metrics["cache_hit"] is True
    assert fake_provider.stream_calls == 1


def test_errors_are_not_cached(fake_provider, monkeypatch):
    def failing_generate(system_prompt, user_message):
        raise ValueError("rechazo")

    monkeypatch.setattr(fake_provider, "generate", failing_generate)

    with pytest.raises(ValueError):
        llm_service.generate_estimation(REQUEST)

    assert len(llm_service.cache) == 0
