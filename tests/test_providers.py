from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services.providers import LLMProviderError, create_provider
from app.services.providers.anthropic_provider import AnthropicProvider
from app.services.providers.openai_provider import OpenAIProvider


def make_settings(**overrides) -> Settings:
    values = {
        "llm_provider": "anthropic",
        "llm_model": "claude-opus-5-5",
        "anthropic_api_key": "fake-anthropic-key",
        "openai_api_key": "fake-openai-key",
        "max_tokens": 1000,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_creates_anthropic_provider():
    provider = create_provider(make_settings(llm_provider="anthropic"))

    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-opus-5-5"


def test_creates_openai_provider():
    provider = create_provider(
        make_settings(llm_provider="openai", llm_model="gpt-4o-mini")
    )

    assert isinstance(provider, OpenAIProvider)
    assert provider.model == "gpt-4o-mini"


def test_missing_api_key_raises_clear_error():
    with pytest.raises(LLMProviderError, match="OPENAI_API_KEY"):
        create_provider(make_settings(llm_provider="openai", openai_api_key=None))


def test_unknown_provider_raises_clear_error():
    with pytest.raises(LLMProviderError, match="desconocido"):
        create_provider(make_settings(llm_provider="gemini"))


def test_anthropic_generate_maps_response(monkeypatch):
    provider = create_provider(make_settings(llm_provider="anthropic"))
    fake_response = SimpleNamespace(
        stop_reason="end_turn",
        model="claude-opus-5-5",
        content=[SimpleNamespace(type="text", text="## Estimación")],
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )
    monkeypatch.setattr(
        provider._client.messages, "create", lambda **kwargs: fake_response
    )

    result = provider.generate("system", "transcripción")

    assert result.text == "## Estimación"
    assert result.provider == "anthropic"
    assert result.input_tokens == 1000
    assert result.output_tokens == 200


def test_openai_generate_maps_response(monkeypatch):
    provider = create_provider(
        make_settings(llm_provider="openai", llm_model="gpt-4o-mini")
    )
    captured = {}
    fake_response = SimpleNamespace(
        model="gpt-4o-mini",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content="## Estimación", refusal=None),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=150),
    )

    def fake_create(**kwargs):
        captured.update(kwargs)
        return fake_response

    monkeypatch.setattr(provider._client.chat.completions, "create", fake_create)

    result = provider.generate("system", "transcripción")

    assert result.text == "## Estimación"
    assert result.provider == "openai"
    assert result.input_tokens == 900
    assert result.output_tokens == 150
    assert captured["messages"][0] == {"role": "system", "content": "system"}


def test_openai_stream_yields_text_and_fills_usage(monkeypatch):
    provider = create_provider(
        make_settings(llm_provider="openai", llm_model="gpt-4o-mini")
    )

    def chunk(content=None, finish_reason=None):
        delta = SimpleNamespace(content=content, refusal=None)
        return SimpleNamespace(
            model="gpt-4o-mini",
            usage=None,
            choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)],
        )

    usage_chunk = SimpleNamespace(
        model="gpt-4o-mini",
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=20),
        choices=[],
    )
    chunks = [chunk("## Esti"), chunk("mación"), chunk(None, "stop"), usage_chunk]
    monkeypatch.setattr(
        provider._client.chat.completions, "create", lambda **kwargs: iter(chunks)
    )

    usage = {}
    pieces = list(provider.stream("system", "transcripción", usage))

    assert pieces == ["## Esti", "mación"]
    assert usage == {"model": "gpt-4o-mini", "input_tokens": 900, "output_tokens": 20}


def test_openai_refusal_raises_value_error(monkeypatch):
    provider = create_provider(
        make_settings(llm_provider="openai", llm_model="gpt-4o-mini")
    )
    fake_response = SimpleNamespace(
        model="gpt-4o-mini",
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=None, refusal="No puedo ayudar"),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=900, completion_tokens=5),
    )
    monkeypatch.setattr(
        provider._client.chat.completions, "create", lambda **kwargs: fake_response
    )

    with pytest.raises(ValueError):
        provider.generate("system", "transcripción")
