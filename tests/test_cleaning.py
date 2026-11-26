"""Limpieza y validación (paso 2.4). Basado en los tests del curso."""

import json
from pathlib import Path

import pandas as pd
import pytest

from app.ingestion.cleaning import clean_budget_records, validate_with_policy

BUDGETS = Path(__file__).resolve().parent.parent / "data/seed/budgets"


def budget(**overrides) -> dict:
    record = {
        "budget_id": "BUDGET-2024-0100",
        "client_name": "Acme",
        "client_code": "CLI-0001",
        "currency": "EUR",
        "total_amount": 1000.0,
        "signed_at": "2024-05-02",
    }
    return {**record, **overrides}


@pytest.fixture(scope="module")
def seed_result():
    records = [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(BUDGETS.glob("*.json"))
    ]
    with pytest.warns(UserWarning):  # el aviso de pandas sobre dayfirst + ISO
        cleaned = clean_budget_records(records)
    return cleaned, validate_with_policy(cleaned)


# --- limpieza: repara, no juzga --------------------------------------------------


@pytest.mark.parametrize("placeholder", ["TBD", "N/A", "", "  -  "])
def test_disguised_nulls_become_na(placeholder):
    df = clean_budget_records([budget(client_name=placeholder)])

    assert pd.isna(df.loc[0, "client_name"])


def test_currency_is_upper_cased_and_amounts_coerced():
    df = clean_budget_records([budget(currency="eur", total_amount="1500")])

    assert df.loc[0, "currency"] == "EUR"
    assert df.loc[0, "total_amount"] == 1500


def test_dedup_keeps_the_latest_signed_version():
    df = clean_budget_records(
        [
            budget(total_amount=100.0, signed_at="2024-01-10"),
            budget(total_amount=200.0, signed_at="2024-06-10"),
        ]
    )

    assert len(df) == 1
    assert df.loc[0, "total_amount"] == 200.0


# --- validación: el contrato decide ------------------------------------------------


def test_valid_rows_pass():
    result = validate_with_policy(clean_budget_records([budget()]))

    assert result.report["valid_rows"] == 1


@pytest.mark.parametrize(
    "dirt",
    [
        {"budget_id": "BUDGET-2024-0200", "total_amount": -50000.0},
        {"budget_id": "BUDGET-2024-0200", "currency": "ARS"},
        {"budget_id": "B-1"},
    ],
)
def test_structural_dirt_is_discarded(dirt):
    result = validate_with_policy(clean_budget_records([budget(), budget(**dirt)]))

    assert (result.report["valid_rows"], result.report["discarded_rows"]) == (1, 1)


def test_an_unparseable_date_goes_to_quarantine_not_to_the_bin():
    result = validate_with_policy(
        clean_budget_records(
            [budget(), budget(budget_id="BUDGET-2024-0300", signed_at="ayer")]
        )
    )

    assert result.report["quarantined_rows"] == 1
    assert result.quarantined.iloc[0]["budget_id"] == "BUDGET-2024-0300"


def test_lazy_validation_collects_every_failure():
    result = validate_with_policy(
        clean_budget_records(
            [
                budget(),
                budget(budget_id="BUDGET-2024-0400", total_amount=-1.0),
                budget(budget_id="BUDGET-2024-0500", currency="ARS"),
            ]
        )
    )

    assert result.report["discarded_rows"] == 2


# --- la demo del curso -------------------------------------------------------------


def test_seed_budgets_reproduce_the_course_demo(seed_result):
    cleaned, result = seed_result

    assert len(cleaned) == 5  # las dos versiones de 0005 colapsan
    assert (
        cleaned.set_index("budget_id").loc["BUDGET-2024-0005", "total_amount"]
        == 32000  # la v2
    )
    assert {
        k: result.report[k]
        for k in ("valid_rows", "quarantined_rows", "discarded_rows")
    } == {
        "valid_rows": 3,
        "quarantined_rows": 1,
        "discarded_rows": 1,
    }
    assert list(result.discarded["budget_id"]) == ["BUDGET-2024-0004"]
    assert list(result.quarantined["budget_id"]) == ["BUDGET-2024-0002"]
    assert "BUDGET-2024-0003" in set(result.valid["budget_id"])  # el «TBD» pasa
