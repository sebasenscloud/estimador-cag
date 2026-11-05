from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest
from app.services.providers import LLMProviderError

client = TestClient(app)

VALID_REQUEST = {
    "description": (
        "El cliente pidió una app de reservas de turnos "
        "con login, calendario y notificaciones por email."
    ),
    "project_type": "mobile_app",
    "detail_level": "medium",
    "output_format": "phases_table",
}

ESTIMATION_TEXT = "## Estimación: App de turnos\n\n**Total estimado: 120 horas**"


def fake_generate_text(system_prompt: str, user_message: str) -> str:
    return ESTIMATION_TEXT


def fake_generate_text_refusal(system_prompt: str, user_message: str) -> str:
    raise ValueError("El modelo no pudo generar la estimación.")


def fake_generate_text_provider_error(system_prompt: str, user_message: str) -> str:
    raise LLMProviderError("API key inválida")


def test_health_check():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_estimate_returns_text_and_prompt_version(monkeypatch):
    monkeypatch.setattr("app.routers.estimations.generate_text", fake_generate_text)

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json() == {"text": ESTIMATION_TEXT, "prompt_version": "v1"}


def test_estimate_sends_rendered_system_and_user_separately(monkeypatch):
    received = {}

    def capture(system_prompt: str, user_message: str) -> str:
        received["system"] = system_prompt
        received["user"] = user_message
        return ESTIMATION_TEXT

    monkeypatch.setattr("app.routers.estimations.generate_text", capture)

    client.post("/api/v1/estimate", json=VALID_REQUEST)

    expected_system, expected_user = render_estimation_prompt(
        EstimationRequest(**VALID_REQUEST), version=settings.prompt_version
    )
    assert received["system"] == expected_system
    assert received["user"] == expected_user
    assert VALID_REQUEST["description"] not in received["system"]
    assert VALID_REQUEST["description"] in received["user"]


def test_estimate_requires_all_fields():
    response = client.post(
        "/api/v1/estimate", json={"description": VALID_REQUEST["description"]}
    )

    assert response.status_code == 422


def test_estimate_rejects_short_description():
    response = client.post(
        "/api/v1/estimate", json={**VALID_REQUEST, "description": "muy corto"}
    )

    assert response.status_code == 422


def test_estimate_rejects_too_long_description():
    response = client.post(
        "/api/v1/estimate", json={**VALID_REQUEST, "description": "a" * 2001}
    )

    assert response.status_code == 422


def test_estimate_rejects_unknown_enum_value():
    response = client.post(
        "/api/v1/estimate", json={**VALID_REQUEST, "project_type": "videojuego"}
    )

    assert response.status_code == 422


def test_estimate_handles_model_refusal(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_text", fake_generate_text_refusal
    )

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 422
    assert "no pudo generar" in response.json()["detail"]


def test_estimate_handles_provider_error(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_text", fake_generate_text_provider_error
    )

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
