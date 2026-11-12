import pytest
from pydantic import ValidationError

from app.sessions import (
    MAX_TURNS,
    ConversationHistory,
    ProjectMetadata,
    SessionNotFoundError,
    SessionStore,
)


def add_turn(history: ConversationHistory, number: int) -> None:
    history.add_turn(f"pregunta {number}", f"respuesta {number}")


def system_from(metadata: ProjectMetadata) -> str:
    return f"system | proyecto={metadata.project_name}"


def contents(history: ConversationHistory, metadata=None) -> list[str]:
    messages = history.to_messages_list(metadata or ProjectMetadata(), system_from)
    return [message["content"] for message in messages]


def test_default_window_is_max_turns():
    assert MAX_TURNS == 6
    assert ConversationHistory().max_turns == MAX_TURNS


def test_messages_list_starts_with_system_and_alternates_roles():
    history = ConversationHistory(max_turns=3)
    add_turn(history, 1)
    add_turn(history, 2)

    messages = history.to_messages_list(ProjectMetadata(), system_from)

    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_window_discards_the_oldest_pairs_beyond_the_limit():
    history = ConversationHistory(max_turns=2)
    for number in range(1, 5):
        add_turn(history, number)

    assert history.turn_count == 2
    assert contents(history)[1:] == [
        "pregunta 3",
        "respuesta 3",
        "pregunta 4",
        "respuesta 4",
    ]


def test_system_prompt_is_always_present():
    history = ConversationHistory(max_turns=1)
    assert contents(history) == ["system | proyecto=None"]

    for number in range(1, 20):
        add_turn(history, number)

    assert contents(history)[0] == "system | proyecto=None"
    assert len(history) == 2


def test_system_prompt_is_regenerated_from_the_current_metadata():
    history = ConversationHistory(max_turns=3)
    add_turn(history, 1)

    before = contents(history, ProjectMetadata())[0]
    after = contents(history, ProjectMetadata(project_name="Turnito"))[0]

    assert before == "system | proyecto=None"
    assert after == "system | proyecto=Turnito"
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
