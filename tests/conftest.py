import re

import pytest

from app.config import settings
from app.schemas import EstimationResult, LLMCallMetrics
from app.services import llm_wrapper
from app.services.llm_wrapper import LLMCall


def llm_call(result, purpose: str = "estimation") -> LLMCall:
    """Envuelve un resultado falso como lo devuelve el wrapper real."""
    return LLMCall(
        result=result,
        metrics=LLMCallMetrics(
            purpose=purpose,
            model="anthropic/claude-haiku-4-5",
            provider="anthropic",
            latency_ms=1200,
            tokens_in=2000,
            tokens_out=500,
            cost_usd=0.0045,
        ),
    )


@pytest.fixture(autouse=True)
def block_real_llm_calls(monkeypatch):
    """Red de seguridad: ningún test puede llamar al LLM real (cuesta dinero).

    Si un test llega a LiteLLM sin haber simulado la llamada, falla en lugar de
    usar la API key del .env. Usa pytest.fail (BaseException) para que ningún
    `except Exception` del código, ni los reintentos de Instructor, lo oculten.
    """

    def forbidden(*args, **kwargs):
        pytest.fail("Un test intentó llamar al LLM real; simulá la llamada.")

    monkeypatch.setattr(llm_wrapper.litellm, "completion", forbidden)
    # La moderación del guardrail de entrada también es una llamada real.
    monkeypatch.setattr(llm_wrapper.litellm, "moderation", forbidden)
    monkeypatch.setattr(settings, "moderation_enabled", False)


@pytest.fixture(autouse=True)
def fake_summarizer(monkeypatch):
    """Summarizer determinístico: lista los turnos que se condensaron.

    Los tests que necesitan otro comportamiento lo reemplazan con monkeypatch.
    """
    from app import compression

    def summarize(system_prompt, user_message, response_model, **kwargs):
        numbers = re.findall(r'<turno numero="(\d+)">', user_message)
        previous = re.search(
            r"<resumen_actual>\s*(.*?)\s*</resumen_actual>", user_message, re.DOTALL
        )[1]
        base = "" if previous == "(vacío)" else previous + " "
        return llm_call(
            response_model(summary=f"{base}Resumen de turnos {', '.join(numbers)}."),
            "summary",
        )

    monkeypatch.setattr(compression, "generate_structured", summarize)


@pytest.fixture
def estimation_result() -> EstimationResult:
    return EstimationResult.model_validate(
        {
            "summary": "SaaS B2B de reservas con panel de administración.",
            "phases": [
                {
                    "name": "Discovery",
                    "description": "Requisitos y diseño.",
                    "duration_weeks": 4,
                    "cost_eur": 8000,
                    "confidence_pct": 80,
                },
                {
                    "name": "Build",
                    "description": "Desarrollo del producto.",
                    "duration_weeks": 8,
                    "cost_eur": 14000,
                    "confidence_pct": 70,
                },
                {
                    "name": "QA + launch",
                    "description": "Pruebas y lanzamiento.",
                    "duration_weeks": 2,
                    "cost_eur": 2000,
                    "confidence_pct": 85,
                },
            ],
            "total_duration_weeks": 14,
            "total_cost_eur": 24000,
            "confidence_pct": 75,
        }
    )
