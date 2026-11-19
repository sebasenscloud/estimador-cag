from app.config import settings
from app.schemas import ProjectType
from evals.dataset import load_dataset


def test_the_golden_dataset_has_16_cases_with_unique_ids():
    cases = load_dataset()

    assert len(cases) == 16
    assert len({case.id for case in cases}) == 16


def test_every_project_type_is_covered():
    assert {case.project_type for case in load_dataset()} == set(ProjectType)


def test_ranges_are_ordered_and_within_the_scope_guardrail():
    for case in load_dataset():
        for rng in (
            case.expected_phase_count_range,
            case.expected_cost_range_eur,
            case.expected_duration_weeks_range,
        ):
            assert rng is None or 0 < rng[0] <= rng[1], case.id
        weeks = case.expected_duration_weeks_range
        assert weeks is None or weeks[1] <= settings.scope_max_weeks


def test_out_of_scope_cases_declare_no_ranges():
    for case in load_dataset():
        if case.expected_out_of_scope:
            assert case.expected_cost_range_eur is None, case.id
