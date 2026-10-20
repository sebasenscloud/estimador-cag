from fastapi.testclient import TestClient

from app.config import settings
from app.main import app

client = TestClient(app)

VALID_TRANSCRIPTION = (
    "En la reunión el cliente pidió una app de reservas de turnos "
    "con login, calendario y notificaciones por email."
)


def fake_generate_estimation(transcript: str) -> dict:
    return {
        "estimation": "## Estimación: App de turnos\n\n**Total estimado: 120 horas**",
        "input_tokens": 1500,
        "output_tokens": 300,
    }


def fake_generate_estimation_refusal(transcript: str) -> dict:
    raise ValueError("El modelo no pudo generar la estimación.")


def test_health_check():
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_estimate_returns_estimation(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_estimation", fake_generate_estimation
    )

    response = client.post(
        "/api/v1/estimate", json={"transcription": VALID_TRANSCRIPTION}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["estimation"].startswith("## Estimación")
    assert body["model"] == settings.llm_model
    assert body["provider"] == settings.llm_provider
    assert body["input_tokens"] == 1500
    assert body["output_tokens"] == 300
    assert "datetime_created" in body


def test_estimate_requires_transcription():
    response = client.post("/api/v1/estimate", json={})

    assert response.status_code == 422


def test_estimate_rejects_short_transcription():
    response = client.post("/api/v1/estimate", json={"transcription": "muy corto"})

    assert response.status_code == 422


def test_estimate_handles_model_refusal(monkeypatch):
    monkeypatch.setattr(
        "app.routers.estimations.generate_estimation",
        fake_generate_estimation_refusal,
    )

    response = client.post(
        "/api/v1/estimate", json={"transcription": VALID_TRANSCRIPTION}
    )

    assert response.status_code == 422
    assert "no pudo generar" in response.json()["detail"]
