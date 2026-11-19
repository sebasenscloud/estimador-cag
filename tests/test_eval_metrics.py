from app.schemas import EstimationResult, Phase
from evals.dataset import GoldenCase
from evals.metrics import (
    ContentRecallMetric,
    CostBoundsMetric,
    SchemaAdherenceMetric,
    run_all_metrics,
)

CASE = GoldenCase(
    id="case-test",
    transcript="We want a B2B CRM for the sales team. React + Postgres.",
    project_type="web_saas",
    expected_in_summary=["CRM"],
    expected_technologies_any_of=["React", "Postgres"],
    expected_phase_count_range=(2, 4),
    expected_cost_range_eur=(10_000, 50_000),
    expected_duration_weeks_range=(4, 12),
)
OUT_OF_SCOPE = CASE.model_copy(update={"expected_out_of_scope": True})


def phase(name="Build", description="Frontend en React", weeks=4, cost=10_000):
    return Phase(
        name=name,
        description=description,
        duration_weeks=weeks,
        cost_eur=cost,
        confidence_pct=70,
    )


def result(phases, summary="Un CRM B2B.", confidence=70, **overrides):
    # model_construct salta los validadores: así se simulan regresiones.
    return EstimationResult.model_construct(
        summary=summary,
        phases=phases,
        total_duration_weeks=overrides.get(
            "weeks", sum(p.duration_weeks for p in phases)
        ),
        total_cost_eur=overrides.get("cost", sum(p.cost_eur for p in phases)),
        confidence_pct=confidence,
    )


GOOD = result([phase("Discovery", weeks=2), phase(), phase("QA", weeks=2)])


def test_a_good_estimate_passes_all_three():
    assert all(metric.passed for metric in run_all_metrics(CASE, GOOD))


def test_schema_adherence_catches_each_regression():
    metric = SchemaAdherenceMetric()

    assert "phases sum" in metric.evaluate(CASE, result([phase()], cost=1)).details
    assert "prefix" in metric.evaluate(CASE, result([phase()], confidence=10)).details
    many = result([phase() for _ in range(5)])
    assert "phase count 5 outside [2, 4]" in metric.evaluate(CASE, many).details


def test_cost_bounds_checks_cost_and_weeks():
    expensive = result([phase(cost=90_000, weeks=20)])

    details = CostBoundsMetric().evaluate(CASE, expensive).details

    assert "cost 90000 EUR outside" in details
    assert "duration 20w outside" in details


def test_out_of_scope_case_needs_the_prefix_and_one_phase():
    metric = CostBoundsMetric()
    declared = result([phase("Relevamiento")], summary="Out of scope: falta info.")

    assert metric.evaluate(OUT_OF_SCOPE, declared).passed
    assert not metric.evaluate(OUT_OF_SCOPE, GOOD).passed
    assert ContentRecallMetric().evaluate(OUT_OF_SCOPE, declared).passed


def test_content_recall_needs_terms_and_any_technology():
    metric = ContentRecallMetric()
    bland = result([phase(description="Desarrollo")], summary="Un sistema de ventas.")

    details = metric.evaluate(CASE, bland).details

    assert "missing in summary: ['CRM']" in details
    assert "none of ['React', 'Postgres']" in details
