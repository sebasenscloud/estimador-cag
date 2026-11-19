import pytest
from fastapi.testclient import TestClient

from app import tiers
from app.config import settings
from app.main import app
from app.prompts.loader import render_estimation_prompt
from app.schemas import DetailLevel, EstimationRequest, OutputFormat, ProjectType
from app.services import session_estimation
from app.sessions import ProjectMetadata, session_store
from app.tiers import TIERS, Tier, resolve_tier, technical_terms
from tests.conftest import llm_call

client = TestClient(app)

REQUEST = EstimationRequest(
    description="El cliente quiere una app de turnos para su cadena de peluquerías.",
    project_type=ProjectType.MOBILE_APP,
    detail_level=DetailLevel.MEDIUM,
    output_format=OutputFormat.PHASES_TABLE,
)

AUDIENCE_MARKERS = {
    "executive": "Riesgos primero",
    "pm": "orientado a hitos",
    "developer": "supuesto técnico más riesgoso",
    "default": "Comportamiento por defecto",
}


@pytest.mark.parametrize(
    ("text", "metadata", "tier", "rule"),
    [
        (
            "We signed the NDA for this CRM.",
            ProjectMetadata(),
            "executive",
            "nda_detected",
        ),
        (
            "Tiene que cumplir GDPR.",
            ProjectMetadata(),
            "executive",
            "regulatory_context",
        ),
        (
            "Backend en FastAPI, frontend React y deploy en Kubernetes.",
            ProjectMetadata(),
            "developer",
            "technical_audience",
        ),
        (
            "Seguimos con la app de turnos.",
            ProjectMetadata(assumed_team_size=2),
            "pm",
            "low_budget_pm",
        ),
        ("Seguimos con la app de turnos.", ProjectMetadata(), "default", "default"),
    ],
)
def test_resolver_rules(text, metadata, tier, rule):
    resolution = resolve_tier(text, metadata)

    assert (resolution.tier, resolution.rule) == (tier, rule)


def test_nda_wins_over_technical_audience():
    text = "Firmamos un NDA. Backend FastAPI, frontend React, deploy en Kubernetes."

    assert resolve_tier(text, ProjectMetadata()).rule == "nda_detected"


def test_one_technical_term_is_not_enough():
    assert technical_terms("Una app con login y una API.") == {"api"}
    assert (
        resolve_tier("Una app con login y una API.", ProjectMetadata()).tier
        == "default"
    )


def test_confidential_and_ccpa_from_the_course_patterns():
    assert resolve_tier("This is confidential.", ProjectMetadata()).tier == "executive"
    assert resolve_tier("Must follow CCPA.", ProjectMetadata()).rule == (
        "regulatory_context"
    )


def test_nda_and_regulation_also_come_from_the_metadata():
    nda = ProjectMetadata(agreed_scope="CRM bajo NDA con el cliente")
    hipaa = ProjectMetadata(mentioned_technologies=["FHIR", "HIPAA"])

    assert resolve_tier("Seguimos.", nda).rule == "nda_detected"
    assert resolve_tier("Seguimos.", hipaa).rule == "regulatory_context"


def test_two_terms_are_enough_and_large_teams_have_no_rule():
    assert resolve_tier("Seguimos.", ProjectMetadata(assumed_team_size=8)).rule == (
        "default"
    )
    assert resolve_tier("Docker y Kafka.", ProjectMetadata()).tier == "developer"


def test_a_broken_predicate_is_skipped(monkeypatch):
    def broken(context):
        raise ValueError("boom")

    monkeypatch.setattr(
        tiers, "_RULES", (tiers.TierRule("broken", Tier.PM, broken), *tiers._RULES)
    )

    assert resolve_tier("We signed the NDA.", ProjectMetadata()).rule == "nda_detected"


def test_override_wins_over_every_rule():
    resolution = resolve_tier("We signed the NDA.", ProjectMetadata(), Tier.DEVELOPER)

    assert (resolution.tier, resolution.rule) == ("developer", "explicit_override")


def test_thresholds_come_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "tier_small_team_size", 3)

    resolution = resolve_tier("Seguimos.", ProjectMetadata(assumed_team_size=3))

    assert resolution.tier == "pm"


@pytest.mark.parametrize("tier", TIERS)
def test_v2_renders_only_the_selected_audience(tier):
    system, _ = render_estimation_prompt(REQUEST, version="v2", tier=tier)
    audience = system[system.index("<audience>") : system.index("</audience>")]

    assert f"El tier resuelto para este turno es {tier}." in audience
    for other, marker in AUDIENCE_MARKERS.items():
        assert (marker in audience) == (other == tier)


def test_v1_prompt_has_no_audience_block():
    system, _ = render_estimation_prompt(REQUEST, version="v1", tier="executive")

    assert "<audience>" not in system


def test_session_tier_changes_per_turn_and_ignores_anchors(
    monkeypatch, estimation_result
):
    """Como en la demo: NDA en el turno 1 → executive; el turno 2 vuelve a default."""
    systems = []
    monkeypatch.setattr(settings, "prompt_version", "v2")
    monkeypatch.setattr(
        session_estimation,
        "generate_structured_from_messages",
        lambda messages, response_model, **kwargs: (
            systems.append(messages[0]["content"]) or llm_call(estimation_result)
        ),
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: (current, None),
    )
    session_id = client.post("/api/v1/sessions").json()["session_id"]
    url = f"/api/v1/sessions/{session_id}/estimate"

    first = client.post(
        url,
        data={
            "transcript": "We signed the NDA: we need a CRM.",
            "project_type": "web_saas",
        },
    ).json()
    second = client.post(
        url,
        data={
            "transcript": "Sumemos reportes mensuales al CRM.",
            "project_type": "web_saas",
        },
    ).json()
    debug = client.get(f"/api/v1/sessions/{session_id}").json()

    assert (first["tier"], first["tier_rule"]) == ("executive", "nda_detected")
    assert first["anchor_rules"] == ["nda"]
    assert (second["tier"], second["tier_rule"]) == ("default", "default")
    assert "es executive" in systems[0]
    assert "es default" in systems[1]
    assert (debug["last_resolved_tier"], debug["last_tier_rule"]) == (
        "default",
        "default",
    )
    session_store.delete(session_id)
