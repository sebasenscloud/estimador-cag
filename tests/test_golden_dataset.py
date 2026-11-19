from app.config import settings
from app.schemas import ProjectType
from evals.dataset import load_golden_dataset


def test_the_golden_dataset_has_16_valid_cases():
    cases = load_golden_dataset()

    assert len(cases) == 16


def test_every_project_type_is_covered():
    types = {case.project_type for case in load_golden_dataset()}

    assert types == set(ProjectType)


def test_duration_ranges_stay_within_the_scope_guardrail():
    for case in load_golden_dataset():
        assert case.expected_duration_weeks_range[1] <= settings.scope_max_weeks
