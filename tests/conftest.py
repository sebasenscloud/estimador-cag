import pytest

from app.schemas import EstimationResult


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
