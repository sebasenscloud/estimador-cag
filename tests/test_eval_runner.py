import json

from app.services import session_estimation
from app.sessions import session_store
from evals import run as runner
from tests.conftest import llm_call


def test_runner_scores_cases_without_calling_the_real_llm(
    monkeypatch, tmp_path, estimation_result
):
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
    sessions_before = len(session_store._sessions)
    output = tmp_path / "actor.json"

    exit_code = runner.main(["--limit", "2", "--output", str(output)])

    report = json.loads(output.read_text())
    assert exit_code in (0, 1)
    assert report["mode"] == "actor"
    assert report["cases_total"] == 2
    assert [row["id"] for row in report["rows"]] == [
        "case-001-web-saas-crm",
        "case-002-mobile-loyalty",
    ]
    assert {m["name"] for m in report["rows"][0]["metrics"]} == {
        "schema_adherence",
        "cost_bounds",
        "content_recall",
    }
    assert len(session_store._sessions) == sessions_before


def test_a_failing_case_does_not_stop_the_run(monkeypatch, tmp_path):
    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(runner, "run_case", boom)
    output = tmp_path / "actor.json"

    assert runner.main(["--limit", "2", "--output", str(output)]) == 1
    rows = json.loads(output.read_text())["rows"]
    assert [row["error"] for row in rows] == ["provider down", "provider down"]


def test_a_guardrail_rejection_counts_as_out_of_scope(tmp_path):
    """case-006 es adversarial: el guardrail lo frena antes del LLM."""
    output = tmp_path / "actor.json"
    case = next(c for c in runner.load_dataset() if c.id.startswith("case-006"))

    with runner.TestClient(runner.app) as client:
        payload, _ = runner.run_case(client, case, "actor")

    assert payload["prompt_version"].startswith("guardrail/")
    assert all(metric.passed for metric in runner.evaluate(case, payload))
    assert not output.exists()


def test_acb_mode_hits_the_acb_endpoint(monkeypatch, tmp_path):
    urls = []

    def fake_run_case(client, case, mode):
        urls.append(mode)
        raise RuntimeError("no LLM")

    monkeypatch.setattr(runner, "run_case", fake_run_case)

    runner.main(["--mode", "acb", "--limit", "1", "--output", str(tmp_path / "a.json")])

    assert urls == ["acb"]
