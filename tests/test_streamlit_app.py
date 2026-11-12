import json
from pathlib import Path

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "streamlit_app.py"

DESCRIPTION = "El cliente necesita una app de turnos con login y calendario."


class FakeStreamResponse:
    """Imita la respuesta de httpx.stream: un context manager con líneas NDJSON."""

    def __init__(self, status_code: int, lines: list[str]):
        self.status_code = status_code
        self._lines = lines
        self.text = "\n".join(lines)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self.text.encode()

    def json(self) -> dict:
        return json.loads(self.text)

    def iter_lines(self):
        yield from self._lines

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test/api/v1/estimate/stream")
            raise httpx.HTTPStatusError("error", request=request, response=self)


def final_line(result) -> str:
    response = {"result": result.model_dump(), "prompt_version": "v1", "cached": False}
    return json.dumps({"type": "final", "response": response})


def partial_line(**fields) -> str:
    return json.dumps({"type": "partial", "partial": fields})


def fake_stream(monkeypatch, response: FakeStreamResponse, sent: list | None = None):
    def stream(method, url, json, timeout):
        if sent is not None:
            sent.append({"method": method, "url": url, "json": json})
        return response

    monkeypatch.setattr(httpx, "stream", stream)


@pytest.fixture
def sent_requests(monkeypatch, estimation_result):
    sent = []
    lines = [
        partial_line(summary="SaaS B2B", phases=[{"name": "Discovery"}]),
        partial_line(
            summary="SaaS B2B de reservas",
            phases=[{"name": "Discovery", "duration_weeks": 4}, {"name": "Build"}],
        ),
        final_line(estimation_result),
    ]
    fake_stream(monkeypatch, FakeStreamResponse(200, lines), sent)
    return sent


def run_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def submit(app: AppTest, output_format_index: int = 0) -> AppTest:
    app.text_area[0].input(DESCRIPTION)
    app.selectbox[2].select_index(output_format_index)
    return app.button[0].click().run(timeout=30)


def test_form_has_all_fields():
    app = run_app()

    assert [area.label for area in app.text_area] == ["Descripción del proyecto"]
    assert [box.label for box in app.selectbox] == [
        "Tipo de proyecto",
        "Nivel de detalle",
        "Formato de salida",
    ]
    assert not app.exception


def test_submit_streams_estimation_request_json(sent_requests):
    app = run_app()
    app.text_area[0].input(DESCRIPTION)
    app.selectbox[0].select_index(1)
    app.button[0].click().run(timeout=30)

    assert len(sent_requests) == 1
    assert sent_requests[0]["method"] == "POST"
    assert sent_requests[0]["url"].endswith("/api/v1/estimate/stream")
    assert sent_requests[0]["json"] == {
        "description": DESCRIPTION,
        "project_type": "web_saas",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    assert not app.exception


def test_final_result_replaces_the_partial_view(sent_requests):
    app = submit(run_app(), output_format_index=0)

    assert len(app.dataframe) == 1
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


def test_http_error_is_shown(monkeypatch):
    body = json.dumps({"detail": "Error al comunicarse con el proveedor"})
    fake_stream(monkeypatch, FakeStreamResponse(502, [body]))

    app = submit(run_app())

    assert "502" in app.error[0].value


def test_error_event_in_the_stream_is_shown(monkeypatch):
    lines = [
        partial_line(summary="SaaS"),
        json.dumps({"type": "error", "detail": "la suma no cierra"}),
    ]
    fake_stream(monkeypatch, FakeStreamResponse(200, lines))

    app = submit(run_app())

    assert "la suma no cierra" in app.error[0].value
    assert not app.dataframe


def test_unreachable_service_is_shown(monkeypatch):
    def unreachable(method, url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "stream", unreachable)

    app = submit(run_app())

    assert "No se pudo conectar" in app.error[0].value


def test_out_of_scope_result_shows_a_warning(monkeypatch, estimation_result):
    degraded = estimation_result.model_copy(
        update={"summary": "Out of scope: no es un proyecto de software."}
    )
    fake_stream(monkeypatch, FakeStreamResponse(200, [final_line(degraded)]))

    app = submit(run_app())

    assert "Out of scope" in app.warning[0].value
    assert not app.dataframe
