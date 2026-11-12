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
def sent_requests(monkeypatch, estimation_result):
    sent = []
    payload = {
        "result": estimation_result.model_dump(),
        "prompt_version": "v1",
        "cached": False,
    }

    def fake_post(url, json, timeout):
        sent.append({"url": url, "json": json})
        return FakeResponse(200, payload)

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
    assert not app.exception


def submit(app: AppTest, output_format_index: int) -> AppTest:
    app.text_area[0].input(DESCRIPTION)
    app.selectbox[2].select_index(output_format_index)
    return app.button[0].click().run(timeout=30)


def test_phases_table_renders_a_dataframe_from_the_data(sent_requests):
    app = submit(run_app(), output_format_index=0)

    table = app.dataframe[0].value
    assert list(table["Fase"]) == ["Discovery", "Build", "QA + launch"]
    assert list(table["Semanas"]) == [4, 8, 2]
    assert [metric.value for metric in app.metric][:3] == [
        "14 semanas",
        "24.000 €",
        "75 %",
    ]


def test_line_items_render_one_item_per_phase(sent_requests):
    app = submit(run_app(), output_format_index=1)

    items = [md.value for md in app.markdown if md.value.startswith("- **Discovery")]
    assert len(items) == 1
    assert items[0].count("\n- **") == 2
    assert not app.dataframe


def test_narrative_renders_a_paragraph_per_phase(sent_requests):
    app = submit(run_app(), output_format_index=2)

    paragraphs = [md.value for md in app.markdown if md.value.startswith("**1.")]
    assert paragraphs and "Discovery" in paragraphs[0]
    assert not app.dataframe


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
