import pytest
from pydantic import ValidationError

from app.sessions import (
    ConversationHistory,
    ProjectMetadata,
    SessionNotFoundError,
    SessionStore,
)


def add_turn(history: ConversationHistory, number: int) -> None:
    history.add_user_message(f"pregunta {number}")
    history.add_assistant_message(f"respuesta {number}")


def contents(history: ConversationHistory) -> list[str]:
    return [message["content"] for message in history.messages()]


def test_history_keeps_system_prompt_first():
    history = ConversationHistory(max_turns=3, system_prompt="Sos un estimador.")
    add_turn(history, 1)

    assert history.messages() == [
        {"role": "system", "content": "Sos un estimador."},
        {"role": "user", "content": "pregunta 1"},
        {"role": "assistant", "content": "respuesta 1"},
    ]


def test_window_discards_oldest_turns_beyond_the_limit():
    history = ConversationHistory(max_turns=2, system_prompt="system")
    for number in range(1, 5):
        add_turn(history, number)

    assert history.turn_count == 2
    assert contents(history) == [
        "system",
        "pregunta 3",
        "respuesta 3",
        "pregunta 4",
        "respuesta 4",
    ]


def test_system_prompt_survives_any_number_of_turns():
    history = ConversationHistory(max_turns=1, system_prompt="system")
    for number in range(1, 20):
        add_turn(history, number)

    assert history.messages()[0] == {"role": "system", "content": "system"}
    assert len(history) == 2


def test_trimming_never_leaves_an_assistant_without_its_user_message():
    history = ConversationHistory(max_turns=1)
    add_turn(history, 1)
    history.add_user_message("pregunta 2")

    assert [message["role"] for message in history.messages()] == ["user"]
    assert contents(history) == ["pregunta 2"]


def test_system_prompt_can_be_replaced_without_touching_the_window():
    history = ConversationHistory(max_turns=3, system_prompt="v1")
    add_turn(history, 1)
    history.set_system_prompt("v2 con metadata nueva")

    assert history.messages()[0]["content"] == "v2 con metadata nueva"
    assert history.turn_count == 1


def test_max_turns_must_be_positive():
    with pytest.raises(ValueError):
        ConversationHistory(max_turns=0)


def test_project_metadata_starts_empty():
    metadata = ProjectMetadata()

    assert metadata.project_name is None
    assert metadata.assumed_team_size is None
    assert metadata.mentioned_technologies == []
    assert metadata.agreed_scope is None


def test_project_metadata_rejects_invalid_team_size():
    with pytest.raises(ValidationError):
        ProjectMetadata(assumed_team_size=0)


def test_store_creates_and_returns_sessions_by_id():
    store = SessionStore(max_turns=4)
    session = store.create()

    assert store.get(session.session_id) is session
    assert session.history.max_turns == 4
    assert session.metadata == ProjectMetadata()


def test_sessions_are_isolated_from_each_other():
    store = SessionStore(max_turns=4)
    first, second = store.create(), store.create()
    add_turn(first.history, 1)
    first.metadata.mentioned_technologies.append("Flutter")

    assert first.session_id != second.session_id
    assert len(second.history) == 0
    assert second.metadata.mentioned_technologies == []


def test_unknown_session_raises_not_found():
    with pytest.raises(SessionNotFoundError):
        SessionStore(max_turns=4).get("no-existe")


def test_deleted_session_is_no_longer_found():
    store = SessionStore(max_turns=4)
    session = store.create()
    store.delete(session.session_id)

    with pytest.raises(SessionNotFoundError):
        store.get(session.session_id)
    assert len(store) == 0


def test_post_sessions_returns_a_new_uuid4_session_id():
    import uuid

    from fastapi.testclient import TestClient

    from app.main import app
    from app.sessions import session_store

    client = TestClient(app)
    first = client.post("/api/v1/sessions")
    second = client.post("/api/v1/sessions")

    assert first.status_code == 201
    session_id = first.json()["session_id"]
    assert first.json() == {"session_id": session_id}
    assert uuid.UUID(session_id).version == 4
    assert session_id != second.json()["session_id"]
    assert session_store.get(session_id).history.turn_count == 0
