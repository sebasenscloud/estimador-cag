import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import session_estimation
from app.services.llm_wrapper import InvalidLLMOutputError
from app.sessions import ProjectMetadata, session_store
from tests.test_attachments import make_docx

client = TestClient(app)

FORM = {
    "transcript": "El cliente quiere una app de turnos para su cadena de peluquerías.",
    "project_type": "mobile_app",
}


@pytest.fixture
def llm_messages(monkeypatch, estimation_result):
    """Guarda el array `messages` de cada llamada al estimador."""
    calls = []

    def fake_generate(messages, response_model):
        calls.append(messages)
        return estimation_result

    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", fake_generate
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: ProjectMetadata(
            project_name="Turnito"
        ),
    )
    return calls


def new_session() -> str:
    return client.post("/api/v1/sessions").json()["session_id"]


def estimate(session_id: str, **extra):
    return client.post(f"/api/v1/sessions/{session_id}/estimate", data=FORM, **extra)


def roles(messages: list[dict]) -> list[str]:
    return [message["role"] for message in messages]


def test_first_turn_sends_only_system_and_user(llm_messages):
    estimate(new_session())

    assert roles(llm_messages[0]) == ["system", "user"]


def test_second_turn_includes_the_previous_pair(llm_messages, estimation_result):
    session_id = new_session()
    estimate(session_id)
    response = estimate(session_id)

    second = llm_messages[1]
    assert roles(second) == ["system", "user", "assistant", "user"]
    assert FORM["transcript"] in second[1]["content"]
    assert second[2]["content"] == estimation_result.model_dump_json()
    assert response.json()["history_turns"] == 2


def test_system_prompt_is_rebuilt_with_the_updated_metadata(llm_messages):
    session_id = new_session()
    estimate(session_id)
    estimate(session_id)

    assert "Turnito" not in llm_messages[0][0]["content"]
    assert "Nombre del proyecto: Turnito" in llm_messages[1][0]["content"]


def test_window_limits_the_turns_sent(llm_messages, monkeypatch):
    session_id = new_session()
    session = session_store.get(session_id)
    session.history = type(session.history)(max_turns=2)

    for _ in range(4):
        estimate(session_id)

    # 2 pares de la ventana + system + user actual
    assert len(llm_messages[-1]) == 1 + 2 * 2 + 1
    assert session.history.turn_count == 2


def test_attachment_text_is_not_stored_in_the_history(llm_messages):
    session_id = new_session()
    estimate(
        session_id,
        files=[("attachments", ("minuta.docx", make_docx(["Dato secreto del PDF"])))],
    )
    estimate(session_id)

    previous_user = llm_messages[1][1]["content"]
    assert "[Adjuntos de este turno: minuta.docx]" in previous_user
    assert "Dato secreto del PDF" not in previous_user


def test_failed_turn_is_not_stored(monkeypatch, llm_messages):
    session_id = new_session()

    def failing(messages, response_model):
        raise InvalidLLMOutputError("la suma no cierra")

    monkeypatch.setattr(
        session_estimation, "generate_structured_from_messages", failing
    )

    response = estimate(session_id)

    assert response.status_code == 502
    assert session_store.get(session_id).history.turn_count == 0
