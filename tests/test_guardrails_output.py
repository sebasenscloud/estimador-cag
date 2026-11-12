from app.config import settings
from app.guardrails.output import enforce_scope_response, out_of_scope_reason
from app.schemas import OUT_OF_SCOPE_PREFIX


def test_normal_result_passes_unchanged(estimation_result):
    assert out_of_scope_reason(estimation_result) is None
    assert enforce_scope_response(estimation_result) is estimation_result


def test_result_declared_out_of_scope_by_the_model_is_kept(estimation_result):
    declared = estimation_result.model_copy(
        update={"summary": f"{OUT_OF_SCOPE_PREFIX} no es un proyecto de software."}
    )

    filtered = enforce_scope_response(declared)

    assert filtered.is_out_of_scope
    assert filtered.summary == declared.summary


def test_too_long_project_is_degraded_with_prefix(estimation_result, monkeypatch):
    monkeypatch.setattr(settings, "scope_max_weeks", 10)

    filtered = enforce_scope_response(estimation_result)

    assert filtered.is_out_of_scope
    assert "más de 10 semanas" in filtered.summary
    assert filtered.summary.endswith(estimation_result.summary)
    assert filtered.phases == estimation_result.phases
