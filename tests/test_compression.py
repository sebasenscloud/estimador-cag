import pytest
from fastapi.testclient import TestClient

from app import compression
from app.compression import apply_compression
from app.config import settings
from app.main import app
from app.services import session_estimation
from app.services.llm_wrapper import LLMProviderError
from app.sessions import ConversationHistory, session_store
from tests.conftest import llm_call

client = TestClient(app)


@pytest.fixture
def fake_estimator(monkeypatch, estimation_result):
    monkeypatch.setattr(settings, "anchor_detection_mode", "heuristic")
    monkeypatch.setattr(
        session_estimation,
        "generate_structured_from_messages",
        lambda messages, response_model, **kwargs: llm_call(estimation_result),
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: (current, None),
    )


def test_demo_eight_turns_with_an_nda_in_turn_one(fake_estimator):
    """La demo del curso: max_turns=6, NDA en el turno 1, refinamientos del 2 al 8."""
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    session_store.get(session_id).history = ConversationHistory(max_turns=6)
    url = f"/api/v1/sessions/{session_id}/estimate"
    rows = []

    for turn in range(1, 9):
        transcript = (
            "We signed the NDA: the client wants a CRM for its sales team."
            if turn == 1
            else f"Refinamiento {turn}: ajustamos detalles del CRM del cliente."
        )
        client.post(url, data={"transcript": transcript, "project_type": "web_saas"})
        debug = client.get(f"/api/v1/sessions/{session_id}").json()
        rows.append(
            (debug["message_count"], debug["anchors_count"], debug["summary_chars"] > 0)
        )

    # Turnos 1-6: la ventana se llena (2 → 12 mensajes), sin anclas ni resumen.
    assert rows[:6] == [(2 * n, 0, False) for n in range(1, 7)]
    # Turno 7: sale el turno 1, que es ancla (nda). Todavía no hay resumen.
    assert rows[6] == (12, 2, False)
    # Turno 8: sale el turno 2, que no es ancla: va al resumen.
    assert rows[7] == (12, 2, True)
    session_store.delete(session_id)


def test_only_anchors_leaving_the_window_do_not_call_the_llm(monkeypatch):
    calls = []
    monkeypatch.setattr(
        compression, "generate_structured", lambda *a, **k: calls.append(a)
    )
    history = ConversationHistory(max_turns=1)
    history.append("We signed the NDA.", "ok", anchor_rules=["nda"])
    history.append("pregunta 2", "respuesta 2")

    assert apply_compression(history) is None
    assert calls == []
    assert history.anchor_count == 2
    assert history.summary == ""


def test_summary_is_rebuilt_from_the_previous_one():
    history = ConversationHistory(max_turns=1)
    for number in range(1, 4):
        history.append(f"pregunta {number}", f"respuesta {number}")
        apply_compression(history)

    # Un solo resumen, rehecho a partir del anterior (no uno por pasada).
    assert history.summary == "Resumen de turnos 1. Resumen de turnos 2."
    assert history.turn_count == 1


def test_summarizer_failure_keeps_the_turns_in_the_window(monkeypatch):
    def failing(*args, **kwargs):
        raise LLMProviderError("timeout")

    monkeypatch.setattr(compression, "generate_structured", failing)
    history = ConversationHistory(max_turns=1)
    history.append("pregunta 1", "respuesta 1")
    history.append("pregunta 2", "respuesta 2")

    assert apply_compression(history) is None
    # No se perdió nada: el turno 1 sigue en la ventana hasta el próximo intento.
    assert history.turn_count == 2
    assert history.summary == ""


def test_summarizer_uses_the_configured_model(monkeypatch):
    seen = {}

    def summarize(system_prompt, user_message, response_model, **kwargs):
        seen.update(kwargs)
        return llm_call(response_model(summary="ok"), "summary")

    monkeypatch.setattr(compression, "generate_structured", summarize)
    monkeypatch.setattr(settings, "summary_model", "anthropic/claude-haiku-4-5")
    history = ConversationHistory(max_turns=1)
    history.append("pregunta 1", "respuesta 1")
    history.append("pregunta 2", "respuesta 2")

    metrics = apply_compression(history)

    assert seen == {"purpose": "summary", "model": "anthropic/claude-haiku-4-5"}
    assert metrics.purpose == "summary"
