import pytest

from app.schemas import EstimationResult
from app.services import llm_wrapper


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
