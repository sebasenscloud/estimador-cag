from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from streamlit.testing.v1 import AppTest

APP_PATH = Path(__file__).parent.parent / "streamlit_app.py"

TRANSCRIPT = "El cliente necesita una app de turnos con login y calendario."


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://test")
            raise httpx.HTTPStatusError("error", request=request, response=self)


class FakeService:
    """Simula el servicio IA: crea sesiones y responde turnos."""

    def __init__(self, estimation_result, turn_status: int = 200):
        self.estimation_result = estimation_result
        self.turn_status = turn_status
        self.sessions_created = 0
        self.turns = []

    def post(self, url, data=None, files=None, timeout=None):
        if url.endswith("/api/v1/sessions"):
            self.sessions_created += 1
            return FakeResponse(201, {"session_id": f"sesion-{self.sessions_created}"})

        self.turns.append(SimpleNamespace(url=url, data=data, files=files))
        if self.turn_status != 200:
            return FakeResponse(self.turn_status, {"detail": "error del servicio"})
        return FakeResponse(
            200,
            {
                "session_id": url.split("/")[-2],
                "history_turns": len(self.turns),
                "result": self.estimation_result.model_dump(),
                "project_metadata": {
                    "project_name": "Turnito",
                    "assumed_team_size": 3,
                    "mentioned_technologies": ["Flutter", "Stripe"],
                    "agreed_scope": "Incluye pagos online.",
                },
                "prompt_version": "v1",
                "attachments": [],
            },
        )


@pytest.fixture
def service(monkeypatch, estimation_result):
    fake = FakeService(estimation_result)
    monkeypatch.setattr(httpx, "post", fake.post)
    return fake


def run_app() -> AppTest:
    return AppTest.from_file(str(APP_PATH)).run(timeout=30)


def send(app: AppTest, text: str = TRANSCRIPT, output_format_index: int = 0):
    app.text_area[0].input(text)
    app.selectbox[2].select_index(output_format_index)
    return app.button(key="FormSubmitter:turn_form-Enviar").click().run(timeout=30)


def sidebar_text(app: AppTest) -> str:
    return "\n".join(
        element.value for element in [*app.sidebar.markdown, *app.sidebar.caption]
    )


def test_page_load_creates_a_session(service):
    app = run_app()

    assert service.sessions_created == 1
    assert app.session_state.session_id == "sesion-1"
    assert "sesion-1" in sidebar_text(app)
    assert not app.exception


def test_form_has_transcript_selectors_and_uploader(service):
    app = run_app()

    assert [area.label for area in app.text_area] == ["Transcripción o mensaje"]
    assert [box.label for box in app.selectbox] == [
        "Tipo de proyecto",
        "Nivel de detalle",
        "Formato de salida",
    ]
    assert len(app.get("file_uploader")) == 1


def test_turn_is_sent_as_multipart_to_the_session(service):
    app = send(run_app())

    turn = service.turns[0]
    assert turn.url.endswith("/api/v1/sessions/sesion-1/estimate")
    assert turn.data == {
        "transcript": TRANSCRIPT,
        "project_type": "mobile_app",
        "detail_level": "medium",
        "output_format": "phases_table",
    }
    assert turn.files == []
    assert not app.exception


def test_conversation_and_memory_are_shown(service):
    app = send(send(run_app()))

    assert len(app.chat_message) == 4
    assert len(app.dataframe) == 2
    sidebar = sidebar_text(app)
    assert "Turnito" in sidebar
    assert "Flutter, Stripe" in sidebar
    assert "3 personas" in sidebar


def test_metadata_starts_empty(service):
    app = run_app()

    assert "Vacía" in sidebar_text(app)


def test_new_conversation_creates_a_session_and_resets_state(service):
    app = send(run_app())
    app.sidebar.button[0].click().run(timeout=30)

    assert service.sessions_created == 2
    assert app.session_state.session_id == "sesion-2"
    assert app.session_state.turns == []
    assert not app.chat_message
    assert "Vacía" in sidebar_text(app)


def test_short_transcript_is_not_sent(service):
    app = send(run_app(), text="corto")

    assert service.turns == []
    assert app.warning


def test_lost_session_suggests_a_new_conversation(monkeypatch, estimation_result):
    fake = FakeService(estimation_result, turn_status=404)
    monkeypatch.setattr(httpx, "post", fake.post)

    app = send(run_app())

    assert "Nueva conversación" in app.error[0].value


def test_service_down_on_load_shows_retry(monkeypatch):
    def unreachable(*args, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", unreachable)

    app = run_app()

    assert "No se pudo crear la sesión" in app.error[0].value
    assert [button.label for button in app.button] == ["Reintentar"]
