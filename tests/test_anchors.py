import pytest
from fastapi.testclient import TestClient

from app import anchors
from app.anchors import (
    AnchorMatch,
    _AnchorClassification,
    detect_anchor,
    detect_anchor_heuristic,
)
from app.config import settings
from app.main import app
from app.services import session_estimation
from app.services.llm_wrapper import LLMProviderError
from app.sessions import ConversationHistory, ProjectMetadata, session_store
from tests.conftest import llm_call

client = TestClient(app)


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("We signed an NDA with the client last week.", "nda"),
        ("Firmamos un acuerdo de confidencialidad con el banco.", "nda"),
        ("We signed the contract yesterday.", "signed_contract"),
        ("Ya firmamos el contrato con la cadena de peluquerías.", "signed_contract"),
        ("The scope is frozen for phase one.", "scope_frozen"),
        ("El alcance quedó cerrado: solo reservas y pagos.", "scope_frozen"),
        ("The budget is capped at 40k EUR.", "budget_locked"),
        ("El presupuesto máximo de este proyecto es 40.000 euros.", "budget_locked"),
        ("Tiene que cumplir GDPR y PCI-DSS.", "compliance"),
        ("It must go live by March 1st.", "deadline_hard"),
        ("Tiene que salir en producción antes del 1 de marzo.", "deadline_hard"),
        ("The audit log is contractually required.", "contractual"),
        ("Es una obligación contractual tener soporte 24/7.", "contractual"),
        ("We agreed to deliver the MVP in two phases.", "explicit_commitment"),
        ("El cliente se comprometió a entregar los diseños.", "explicit_commitment"),
    ],
)
def test_heuristic_rules_detect_hard_commitments(text, rule):
    match = detect_anchor_heuristic(text)

    assert match.is_anchor
    assert rule in match.matched_rules


@pytest.mark.parametrize(
    "text",
    [
        "Queremos una app de turnos para peluquerías.",
        "¿Cuánto costaría agregar pagos online?",
        "Quizás más adelante sumemos notificaciones.",
        "Ese es el diseño final que nos gusta.",  # "final" suelto no ancla
        "Es un requisito importante para el cliente.",  # "required/requisito" suelto tampoco
        "El presupuesto todavía no está definido.",
    ],
)
def test_ordinary_messages_are_not_anchored(text):
    assert detect_anchor_heuristic(text) == AnchorMatch(is_anchor=False)


def test_every_rule_that_fires_is_reported():
    match = detect_anchor_heuristic(
        "We signed the contract, the budget is capped at 40k and GDPR applies."
    )

    assert match.matched_rules == ["signed_contract", "budget_locked", "compliance"]


def test_heuristic_mode_never_calls_the_llm(monkeypatch):
    monkeypatch.setattr(settings, "anchor_detection_mode", "heuristic")

    match, metrics = detect_anchor("Ya firmamos el contrato.")

    assert match.matched_rules == ["signed_contract"]
    assert metrics is None


def test_llm_mode_uses_the_classifier(monkeypatch):
    monkeypatch.setattr(settings, "anchor_detection_mode", "llm")
    calls = []

    def fake_generate(system_prompt, user_message, response_model, **kwargs):
        calls.append((user_message, response_model, kwargs.get("purpose")))
        return llm_call(
            _AnchorClassification(
                is_anchor=True, reason="El cliente fijó un tope de 40 mil euros."
            ),
            "anchor_detection",
        )

    monkeypatch.setattr(anchors, "generate_structured", fake_generate)

    match, metrics = detect_anchor("No podemos pasarnos de los 40 mil, ni un euro más.")

    assert match == AnchorMatch(
        is_anchor=True,
        matched_rules=["llm"],
        reason="El cliente fijó un tope de 40 mil euros.",
    )
    assert metrics.purpose == "anchor_detection"
    assert calls[0][1] is _AnchorClassification


def test_llm_mode_falls_back_to_rules_if_the_classifier_fails(monkeypatch):
    monkeypatch.setattr(settings, "anchor_detection_mode", "llm")

    def failing(*args, **kwargs):
        raise LLMProviderError("timeout")

    monkeypatch.setattr(anchors, "generate_structured", failing)

    assert detect_anchor("Ya firmamos el contrato.") == (
        AnchorMatch(is_anchor=True, matched_rules=["signed_contract"]),
        None,
    )


def add(history: ConversationHistory, number: int, anchor_rule=None):
    rules = [anchor_rule] if anchor_rule else []
    return history.add_turn(f"pregunta {number}", f"respuesta {number}", rules)


def user_contents(history: ConversationHistory) -> list[str]:
    messages = history.to_messages_list(ProjectMetadata(), lambda metadata: "system")
    return [m["content"] for m in messages if m["role"] == "user"]


def test_anchored_turn_survives_the_window():
    history = ConversationHistory(max_turns=2)
    add(history, 1, anchor_rule="signed_contract")
    for number in range(2, 6):
        add(history, number)

    assert user_contents(history) == ["pregunta 1", "pregunta 4", "pregunta 5"]
    assert history.anchor_count == 1
    assert history.turn_count == 2


def test_assistant_answer_travels_with_its_anchored_user_message():
    history = ConversationHistory(max_turns=1)
    add(history, 1, anchor_rule="nda")
    add(history, 2)

    messages = history.to_messages_list(ProjectMetadata(), lambda metadata: "system")

    assert [m["content"] for m in messages] == [
        "system",
        "pregunta 1",
        "respuesta 1",
        "pregunta 2",
        "respuesta 2",
    ]


def test_non_anchored_turn_leaving_the_window_is_returned():
    history = ConversationHistory(max_turns=1)
    add(history, 1)

    evicted = add(history, 2)

    assert evicted.number == 1
    assert evicted.user["content"] == "pregunta 1"


def test_anchors_have_their_own_limit():
    history = ConversationHistory(max_turns=1, max_anchors=2)
    for number in range(1, 5):
        add(history, number, anchor_rule="explicit_commitment")
    add(history, 5)

    # Ventana: turno 5. Anclas conservadas: las 2 más recientes que salieron (3 y 4).
    assert user_contents(history) == ["pregunta 3", "pregunta 4", "pregunta 5"]
    assert history.anchor_count == 2


def test_session_turn_reports_and_keeps_the_anchor(monkeypatch, estimation_result):
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
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    url = f"/api/v1/sessions/{session_id}/estimate"

    anchored = client.post(
        url,
        data={
            "transcript": "Ya firmamos el contrato: el alcance es reservas y pagos.",
            "project_type": "mobile_app",
        },
    ).json()
    plain = client.post(
        url,
        data={
            "transcript": "Agregá notificaciones por email.",
            "project_type": "mobile_app",
        },
    ).json()
    debug = client.get(f"/api/v1/sessions/{session_id}").json()

    assert anchored["anchor_rules"] == ["signed_contract"]
    assert plain["anchor_rules"] == []
    assert debug["anchors_count"] == 1
    session_store.delete(session_id)


def test_llm_mode_can_decide_not_to_anchor(monkeypatch):
    monkeypatch.setattr(settings, "anchor_detection_mode", "llm")
    monkeypatch.setattr(
        anchors,
        "generate_structured",
        lambda *args, **kwargs: llm_call(
            _AnchorClassification(is_anchor=False, reason="Es una pregunta."),
            "anchor_detection",
        ),
    )

    match, _ = detect_anchor("¿Y si sumamos pagos online?")

    assert match == AnchorMatch(
        is_anchor=False, matched_rules=[], reason="Es una pregunta."
    )


def test_llm_reason_is_limited_to_200_characters():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        _AnchorClassification(is_anchor=True, reason="x" * 201)
