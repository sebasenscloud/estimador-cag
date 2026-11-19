import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import settings
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.schemas.critic import CriticFeedback, CriticIssue
from app.services import boss, session_estimation
from app.services import critic as critic_service
from app.services.llm_wrapper import LLMProviderError
from app.sessions import ProjectMetadata, session_store
from tests.conftest import llm_call

client = TestClient(app)

MATH = CriticIssue(
    category="math_error",
    severity="critical",
    field_path="total_cost_eur",
    description="El total no coincide con la suma de las fases.",
    suggested_fix="Recalculá el total.",
)
ACCEPT = CriticFeedback(verdict="accept", confidence_in_review=90)
ITERATE = CriticFeedback(
    verdict="needs_iteration", issues=[MATH], confidence_in_review=80
)
REJECT = CriticFeedback(verdict="reject", issues=[MATH], confidence_in_review=70)


# --- Crítico: schema ----------------------------------------------------------


def test_needs_iteration_requires_a_blocking_issue():
    minor = MATH.model_copy(update={"severity": "minor"})

    with pytest.raises(ValidationError):
        CriticFeedback(
            verdict="needs_iteration", issues=[minor], confidence_in_review=50
        )
    with pytest.raises(ValidationError):
        CriticFeedback(verdict="reject", confidence_in_review=50)


def test_critic_failure_falls_back_to_accept_with_zero_confidence(
    monkeypatch, estimation_result
):
    def failing(*args, **kwargs):
        raise LLMProviderError("timeout")

    monkeypatch.setattr(critic_service, "generate_structured", failing)

    feedback, metrics = critic_service.review(
        "un CRM", ProjectMetadata(), "default", estimation_result, "medium"
    )

    assert (feedback.verdict, feedback.confidence_in_review) == ("accept", 0)
    assert metrics is None


# --- Boss: la tabla de decisiones --------------------------------------------


@pytest.mark.parametrize(
    ("review", "left", "decision"),
    [
        (ACCEPT, 2, "accept"),
        (REJECT, 2, "synthesize"),
        (ITERATE, 1, "iterate"),
        (ITERATE, 0, "synthesize"),
    ],
)
def test_boss_decision_table(review, left, decision):
    assert boss.decide(review, iterations_left=left) == decision


def scripted(reviews):
    """Crítico falso que devuelve los veredictos en orden."""
    queue = list(reviews)
    return lambda draft: queue.pop(0)


def test_boss_iterates_with_feedback_then_accepts(estimation_result):
    seen = []

    def actor(feedback):
        seen.append(feedback)
        return estimation_result

    result, trace = boss.run(actor, scripted([ITERATE, ACCEPT]), max_iterations=3)

    assert seen == [None, ITERATE]
    assert result == estimation_result
    assert (trace.final_decision, trace.iterations_run) == ("accept", 2)
    assert trace.iterations[0].issue_summary == [
        "[critical] math_error @ total_cost_eur"
    ]


def test_boss_synthesizes_when_rounds_run_out(estimation_result):
    result, trace = boss.run(
        lambda feedback: estimation_result, scripted([ITERATE] * 3), max_iterations=3
    )

    assert (trace.final_decision, trace.iterations_run) == ("synthesize", 3)
    assert result.summary.startswith(boss.CAVEATS_HEADER)
    assert estimation_result.summary in result.summary
    assert result.confidence_pct == max(40, estimation_result.confidence_pct // 2)


def test_synthesize_has_a_confidence_floor_and_keeps_out_of_scope_first(
    estimation_result,
):
    low = estimation_result.model_copy(update={"confidence_pct": 50})
    out = estimation_result.model_copy(
        update={"summary": "Out of scope: falta info.", "confidence_pct": 20}
    )

    assert boss.synthesize(low, REJECT).confidence_pct == 40
    kept = boss.synthesize(out, REJECT)
    assert kept.is_out_of_scope
    assert kept.confidence_pct == 20


# --- Prompt v3 -----------------------------------------------------------------

REQUEST = EstimationRequest(
    description="Queremos un CRM B2B para el equipo comercial.",
    project_type=ProjectType.WEB_SAAS,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)


def test_v3_adds_critic_feedback_only_when_there_is_feedback():
    _, plain = render_estimation_prompt(REQUEST, version="v3")
    _, v2 = render_estimation_prompt(REQUEST, version="v2")
    _, with_feedback = render_estimation_prompt(
        REQUEST, version="v3", critic_feedback=ITERATE
    )

    assert plain == v2
    assert "<critic_feedback>" in with_feedback
    assert "math_error en `total_cost_eur`" in with_feedback
    assert "sugerencia: Recalculá el total." in with_feedback


# --- Endpoint ------------------------------------------------------------------


def test_estimate_acb_endpoint_runs_the_loop_and_stores_one_turn(
    monkeypatch, estimation_result
):
    actor_messages = []
    reviews = [ITERATE, ACCEPT]
    monkeypatch.setattr(settings, "prompt_version", "v3")
    monkeypatch.setattr(
        session_estimation,
        "generate_structured_from_messages",
        lambda messages, response_model, **kwargs: (
            actor_messages.append(str(messages[-1]["content"]))
            or llm_call(estimation_result)
        ),
    )
    monkeypatch.setattr(
        critic_service,
        "generate_structured",
        lambda *args, **kwargs: llm_call(reviews.pop(0), "critic"),
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: (current, None),
    )
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{session_id}/estimate-acb",
        data={
            "transcript": "Queremos un CRM B2B para ventas.",
            "project_type": "web_saas",
        },
    ).json()
    debug = client.get(f"/api/v1/sessions/{session_id}").json()

    assert body["acb"]["final_decision"] == "accept"
    assert body["acb"]["iterations_run"] == 2
    assert [c["purpose"] for c in body["llm_calls"]] == [
        "estimation",
        "critic",
        "estimation",
        "critic",
    ]
    assert "<critic_feedback>" not in actor_messages[0]
    assert "<critic_feedback>" in actor_messages[1]
    # Los borradores no se guardan: un solo turno en la sesión.
    assert debug["total_turns"] == 1
    stored = session_store.get(session_id).history._window[0]
    assert "<critic_feedback>" not in str(stored)
    session_store.delete(session_id)


def test_actor_endpoint_has_no_acb_trace(monkeypatch, estimation_result):
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

    body = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={
            "transcript": "Queremos un CRM B2B para ventas.",
            "project_type": "web_saas",
        },
    ).json()

    assert body["acb"] is None
    session_store.delete(session_id)
