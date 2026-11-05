from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "streamlit_app.py"

DESCRIPTION = "El cliente necesita una app de turnos con login y calendario."


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test/api/v1/estimate")
            raise httpx.HTTPStatusError("error", request=request, response=self)


@pytest.fixture
def sent_requests(monkeypatch):
    sent = []

    def fake_post(url, json, timeout):
        sent.append({"url": url, "json": json})
        return FakeResponse(200, {"text": "## Estimación OK", "prompt_version": "v2"})

    monkeypatch.setattr(httpx, "post", fake_post)
    return sent


def run_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def test_form_has_all_fields():
    app = run_app()

    assert [area.label for area in app.text_area] == ["Descripción del proyecto"]
    assert [box.label for box in app.selectbox] == [
        "Tipo de proyecto",
        "Nivel de detalle",
        "Formato de salida",
    ]
    assert not app.exception


def test_submit_posts_estimation_request_json(sent_requests):
    app = run_app()
    app.text_area[0].input(DESCRIPTION)
    app.selectbox[0].select_index(1)
    app.button[0].click().run(timeout=30)

    assert len(sent_requests) == 1
    assert sent_requests[0]["url"].endswith("/api/v1/estimate")
    assert sent_requests[0]["json"] == {
        "description": DESCRIPTION,
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    assert "## Estimación OK" in [markdown.value for markdown in app.markdown]


def test_short_description_is_not_sent(sent_requests):
    app = run_app()
    app.text_area[0].input("corto")
    app.button[0].click().run(timeout=30)

    assert sent_requests == []
    assert app.warning


def test_service_error_is_shown(monkeypatch):
    def failing_post(url, json, timeout):
        return FakeResponse(502, {"detail": "Error al comunicarse con el proveedor"})

    monkeypatch.setattr(httpx, "post", failing_post)
    app = run_app()
    app.text_area[0].input(DESCRIPTION)
    app.button[0].click().run(timeout=30)

    assert "502" in app.error[0].value


def test_unreachable_service_is_shown(monkeypatch):
    def unreachable_post(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", unreachable_post)
    app = run_app()
    app.text_area[0].input(DESCRIPTION)
    app.button[0].click().run(timeout=30)

    assert "No se pudo conectar" in app.error[0].value
