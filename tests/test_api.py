import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas import EstimationRequest, EstimationResult
from app.services.llm_service import InvalidLLMOutputError, LLMProviderError

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


@pytest.fixture
def received(monkeypatch, estimation_result):
    calls = {}

    def fake_generate_structured(system_prompt, user_message, response_model):
        calls.update(
            system=system_prompt, user=user_message, response_model=response_model
        )
        return estimation_result, False

    monkeypatch.setattr(
        "app.routers.estimations.generate_structured", fake_generate_structured
    )
    return calls


def fail_with(monkeypatch, error):
    def failing(system_prompt, user_message, response_model):
        raise error

    monkeypatch.setattr("app.routers.estimations.generate_structured", failing)


def test_health_check():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_estimate_returns_structured_result(received, estimation_result):
    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["result"] == estimation_result.model_dump()
    assert body["prompt_version"] == "v1"
    assert body["cached"] is False


def test_estimate_sends_rendered_prompts_and_schema(received):
    client.post("/api/v1/estimate", json=VALID_REQUEST)

    expected_system, expected_user = render_estimation_prompt(
        EstimationRequest(**VALID_REQUEST), version=settings.prompt_version
    )
    assert received["system"] == expected_system
    assert received["user"] == expected_user
    assert received["response_model"] is EstimationResult
    assert VALID_REQUEST["description"] not in received["system"]


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


def test_invalid_model_output_returns_502(monkeypatch):
    fail_with(monkeypatch, InvalidLLMOutputError("la suma no cierra"))

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
    assert "la suma no cierra" in response.json()["detail"]


def test_provider_error_returns_502(monkeypatch):
    fail_with(monkeypatch, LLMProviderError("API key inválida"))

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
