from fastapi.testclient import TestClient

from app.main import app
from app.schemas import EstimationRequest, EstimationResponse
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


def fake_generate_estimation(request: EstimationRequest) -> EstimationResponse:
    return EstimationResponse(
        text="## Estimación: App de turnos\n\n**Total estimado: 120 horas**",
        prompt_version="v-test",
    )


def fake_generate_estimation_refusal(request: EstimationRequest):
    raise ValueError("El modelo no pudo generar la estimación.")


def fake_generate_estimation_provider_error(request: EstimationRequest):
    raise LLMProviderError("API key inválida")


def test_health_check():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_estimate_returns_estimation(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_estimation", fake_generate_estimation
    )

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 200
    assert response.json() == {
        "text": "## Estimación: App de turnos\n\n**Total estimado: 120 horas**",
        "prompt_version": "v-test",
    }


def test_estimate_receives_typed_request(monkeypatch):
    received = {}

    def capture(request: EstimationRequest) -> EstimationResponse:
        received["request"] = request
        return fake_generate_estimation(request)

    monkeypatch.setattr("app.routers.estimations.generate_estimation", capture)

    client.post("/api/v1/estimate", json=VALID_REQUEST)

    request = received["request"]
    assert isinstance(request, EstimationRequest)
    assert request.project_type.value == "mobile_app"
    assert request.output_format.value == "phases_table"


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
        "app.routers.estimations.generate_estimation",
        fake_generate_estimation_refusal,
    )

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 422
    assert "no pudo generar" in response.json()["detail"]


def test_estimate_handles_provider_error(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_estimation",
        fake_generate_estimation_provider_error,
    )

    response = client.post("/api/v1/estimate", json=VALID_REQUEST)

    assert response.status_code == 502
