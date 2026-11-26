import csv

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import TurnObservation
from app.services import session_estimation
from evals.stress import run as runner
from evals.stress.metrics import (
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
)
from evals.stress.scenarios import all_scenarios
from tests.conftest import llm_call

client = TestClient(app)


def observation(**overrides) -> TurnObservation:
    values = {
        "turn_index": 2,
        "session_id": "s",
        "enriched_transcript_chars": 100,
        "attachments_total_chars": 0,
        "messages_in_window": 4,
        "anchors_count": 0,
        "summary_chars": 0,
        "tokens_in": 3000,
        "tokens_out": 700,
        "cost_usd": 0.01,
        "latency_ms": 5000,
        "llm_calls": 2,
    }
    return TurnObservation(**{**values, **overrides})


@pytest.fixture
def fake_llm(monkeypatch, estimation_result):
    monkeypatch.setattr(
        session_estimation,
        "generate_structured_from_messages",
        lambda messages, response_model, **kwargs: llm_call(estimation_result),
    )
    monkeypatch.setattr(
        session_estimation,
        "extract_project_metadata",
        lambda current, turn_content, estimation: (
            current.model_copy(update={"project_name": "Nimbus"}),
            None,
        ),
    )


# --- escenarios ------------------------------------------------------------


def test_three_scenarios_of_twenty_turns_tracking_the_first_fact():
    scenarios = all_scenarios()

    assert [s.name for s in scenarios] == ["growing", "pivot", "contradiction"]
    assert all(len(s.turns) >= 20 for s in scenarios)
    assert all(s.turns[0].fact_introduced for s in scenarios)


# --- métricas --------------------------------------------------------------


def test_latency_and_cost_budgets():
    assert LatencyBudgetMetric(8000).evaluate(observation(latency_ms=8000)).passed
    assert not LatencyBudgetMetric(8000).evaluate(observation(latency_ms=9000)).passed
    assert CostBudgetMetric(0.02).evaluate(observation(cost_usd=0.02)).passed
    assert not CostBudgetMetric(0.02).evaluate(observation(cost_usd=0.03)).passed
    assert not CostBudgetMetric(0.02).evaluate(observation(cost_usd=None)).passed


def test_memory_drift_scopes_the_search():
    snapshot = {
        "project_metadata": {"project_name": "Nimbus", "mentioned_technologies": []},
        "summary": "El cliente usa React.",
        # El log guarda valores viejos: no cuenta como memoria del modelo.
        "metadata_log": [{"old": "Okta"}],
    }

    assert MemoryDriftMetric("nimbus", "project_name").evaluate(snapshot).passed
    assert MemoryDriftMetric("React", "summary").evaluate(snapshot).passed
    assert not MemoryDriftMetric("React", "technologies").evaluate(snapshot).passed
    assert not MemoryDriftMetric("Okta", "any").evaluate(snapshot).passed


# --- telemetría en la respuesta --------------------------------------------


def test_session_response_carries_the_turn_observation(fake_llm):
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    body = client.post(
        f"/api/v1/sessions/{session_id}/estimate",
        data={
            "transcript": "Queremos un CRM B2B para ventas.",
            "project_type": "web_saas",
        },
    ).json()
    debug = client.get(f"/api/v1/sessions/{session_id}").json()

    obs = body["observation"]
    assert obs["turn_index"] == 1
    assert (obs["tokens_in"], obs["tokens_out"]) == (2000, 500)
    assert obs["messages_in_window"] == 2
    assert obs["llm_calls"] == 1
    assert obs["cost_usd"] == pytest.approx(0.0045)
    assert "summary" in debug


# --- runner ----------------------------------------------------------------


def test_runner_writes_one_row_per_turn(fake_llm, tmp_path):
    output = tmp_path / "results.csv"

    runner.main(
        [
            "--scenarios",
            "growing",
            "--attachment-sizes",
            "0",
            "--repeats",
            "1",
            "--output",
            str(output),
        ]
    )

    rows = list(csv.DictReader(output.open()))
    assert len(rows) == 20
    assert list(rows[0]) == runner.CSV_COLUMNS
    assert rows[0]["memory_drift_passed"] == ""  # el turno 1 no se mide
    assert rows[1]["memory_drift_passed"] == "True"  # "Nimbus" sigue vivo
    assert {row["error"] for row in rows} == {""}


def test_runner_stops_at_the_spending_cap(fake_llm, tmp_path, capsys):
    output = tmp_path / "results.csv"

    runner.main(
        [
            "--scenarios",
            "growing",
            "--attachment-sizes",
            "0",
            "--repeats",
            "1",
            "--max-total-usd",
            "0.01",
            "--output",
            str(output),
        ]
    )

    rows = list(csv.DictReader(output.open()))
    assert len(rows) == 3  # 3 × 0.0045 supera 0.01
    assert "Corrida cortada" in capsys.readouterr().out


def test_runner_fails_before_spending_if_a_pdf_is_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "FIXTURES_DIR", tmp_path)

    with pytest.raises(FileNotFoundError):
        runner.main(["--attachment-sizes", "5", "--output", str(tmp_path / "r.csv")])
