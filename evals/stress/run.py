"""Runner del stress test: mide con números dónde se rompe el CAG.

Portado de evals/stress/run.py del curso. Recorre
escenarios × tamaños de adjunto × repeticiones. En cada turno:

1. POST /api/v1/sessions/{id}/estimate con el texto del turno y el PDF de
   prueba (o sin adjunto, para el tamaño 0).
2. Lee response.observation (telemetría del turno): sin parsear logs.
3. GET /api/v1/sessions/{id}: snapshot de la memoria para MemoryDriftMetric.
4. Escribe una fila en el CSV con la telemetría y los tres veredictos.

El entregable es el CSV; con él se completa a mano evals/stress/REPORT.md.

ATENCIÓN: llama al LLM real. Cada turno cuesta ~USD 0,01-0,05 (medido: USD 0,53 por 20 turnos sin adjuntos). La matriz
completa (3 × 5 × 3 × 20 = 900 turnos) ronda los USD 25-35. --max-total-usd
corta la corrida si el gasto acumulado lo supera (por defecto 5).

    uv run python -m evals.stress.fixtures.build_pdfs   # una vez
    uv run python -m evals.stress.run --scenarios growing --attachment-sizes 0 --repeats 1
    uv run python -m evals.stress.run --attachment-sizes 0,20 --repeats 1

Diferencias con el curso: tope de gasto acumulado; costo del turno = todas
sus llamadas; sin cache_hit_kind (las sesiones no usan cache) y con la
columna llm_calls.
"""

import argparse
import csv
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx

from app.schemas import TurnObservation
from evals.stress.metrics import (
    CostBudgetMetric,
    LatencyBudgetMetric,
    MemoryDriftMetric,
)
from evals.stress.scenarios import Scenario, ScenarioTurn, get_scenario

API = "/api/v1/sessions"
FIXTURES_DIR = Path(__file__).parent / "fixtures"
DEFAULT_OUTPUT = Path(__file__).parent / "results.csv"

CSV_COLUMNS = [
    "scenario",
    "attachment_size_kb",
    "repeat",
    "turn_index",
    "session_id",
    "enriched_transcript_chars",
    "attachments_total_chars",
    "messages_in_window",
    "anchors_count",
    "summary_chars",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "latency_ms",
    "llm_calls",
    "wall_clock_ms",
    "last_resolved_tier",
    "latency_budget_passed",
    "cost_budget_passed",
    "memory_drift_passed",
    "tracked_fact",
    "error",
]


class BudgetExceeded(Exception):
    pass


@contextmanager
def open_client(http_base_url: str | None) -> Iterator[Any]:
    """--http → httpx contra un servicio real; si no, TestClient en proceso."""
    if http_base_url:
        with httpx.Client(base_url=http_base_url, timeout=300.0) as client:
            yield client
        return
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        yield client


def form_body(scenario: Scenario, turn: ScenarioTurn) -> dict[str, str]:
    return {
        "transcript": turn.transcript,
        "project_type": scenario.project_type.value,
        "detail_level": scenario.detail_level.value,
        "output_format": scenario.output_format.value,
    }


def attachment_files(size_kb: int) -> list | None:
    if size_kb == 0:
        return None
    path = FIXTURES_DIR / f"attach_{size_kb}kb.pdf"
    if not path.exists():
        raise FileNotFoundError(
            f"Falta {path}: corré `uv run python -m evals.stress.fixtures.build_pdfs`."
        )
    return [("attachments", (path.name, path.read_bytes(), "application/pdf"))]


def execute_turn(client, session_id: str, scenario: Scenario, turn, files):
    """Un POST de estimación y un GET del snapshot.

    Devuelve (observation, snapshot, wall_clock_ms). wall_clock_ms incluye
    todo el turno (extracción, metadata, resumen); observation.latency_ms solo
    la estimación. La diferencia es el costo de la memoria.
    """
    start = time.perf_counter()
    response = client.post(
        f"{API}/{session_id}/estimate", data=form_body(scenario, turn), files=files
    )
    wall_ms = int((time.perf_counter() - start) * 1000)
    response.raise_for_status()
    observation = TurnObservation(**response.json()["observation"])

    snapshot = client.get(f"{API}/{session_id}")
    snapshot.raise_for_status()
    return observation, snapshot.json(), wall_ms


def run_session(
    client,
    scenario: Scenario,
    size_kb: int,
    repeat: int,
    latency_metric: LatencyBudgetMetric,
    cost_metric: CostBudgetMetric,
    writer: csv.DictWriter,
    spent: list[float],
    max_total_usd: float,
) -> int:
    """Recorre el escenario completo en una sesión. Devuelve filas escritas."""
    files = attachment_files(size_kb)
    session_id = client.post(API).json()["session_id"]

    # Se rastrea el dato del turno 1 en toda la sesión: "¿el nombre del
    # proyecto del principio sigue vivo en el turno N?".
    first = scenario.turns[0]
    drift = (
        MemoryDriftMetric(first.fact_introduced, first.fact_field)
        if first.fact_introduced
        else None
    )

    rows = 0
    try:
        for turn in scenario.turns:
            if spent[0] >= max_total_usd:
                raise BudgetExceeded(
                    f"gasto acumulado USD {spent[0]:.2f} >= tope {max_total_usd:.2f}"
                )
            row: dict[str, Any] = {
                "scenario": scenario.name,
                "attachment_size_kb": size_kb,
                "repeat": repeat,
                "tracked_fact": first.fact_introduced or "",
            }
            try:
                observation, snapshot, wall_ms = execute_turn(
                    client, session_id, scenario, turn, files
                )
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
                writer.writerow(row)
                # Un 5xx a mitad de la conversación sesgaría el drift de los
                # turnos siguientes: se corta esta sesión.
                return rows + 1

            spent[0] += observation.cost_usd or 0
            # El turno 1 se "recuerda" a sí mismo: el drift se mide desde el 2.
            drift_passed = (
                ""
                if drift is None or observation.turn_index == 1
                else drift.evaluate(snapshot).passed
            )
            row.update(
                {
                    **observation.model_dump(),
                    "wall_clock_ms": wall_ms,
                    "latency_budget_passed": latency_metric.evaluate(
                        observation
                    ).passed,
                    "cost_budget_passed": cost_metric.evaluate(observation).passed,
                    "memory_drift_passed": drift_passed,
                    "error": "",
                }
            )
            writer.writerow(row)
            rows += 1
    finally:
        # En proceso, la sesión se borra del store (TestClient hereda de
        # httpx.Client, por eso se compara el tipo exacto).
        if type(client) is not httpx.Client:
            from app.sessions import session_store

            session_store.delete(session_id)
    return rows


def percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(pct * (len(ordered) - 1)))]


def print_summary(csv_path: Path) -> None:
    """P50/P95 de latencia, costo total y % de drift por (escenario, tamaño)."""
    groups: dict[tuple[str, int], list[dict[str, str]]] = {}
    with csv_path.open() as fh:
        for row in csv.DictReader(fh):
            if not row.get("error"):
                key = (row["scenario"], int(row["attachment_size_kb"]))
                groups.setdefault(key, []).append(row)

    header = f"{'scenario':<14} {'kb':>4} {'n':>4} {'P50 ms':>8} {'P95 ms':>8} {'total $':>9} {'drift%':>7}"
    print(
        "\nResumen (escenario × tamaño de adjunto)\n"
        + header
        + "\n"
        + "-" * len(header)
    )
    for (scenario, size_kb), rows in sorted(groups.items()):
        latencies = [int(r["latency_ms"]) for r in rows]
        cost = sum(float(r["cost_usd"] or 0) for r in rows)
        drifts = [r["memory_drift_passed"] for r in rows if r["memory_drift_passed"]]
        drift_pct = 100 * drifts.count("True") / len(drifts) if drifts else 0.0
        print(
            f"{scenario:<14} {size_kb:>4} {len(rows):>4} {percentile(latencies, 0.5):>8} "
            f"{percentile(latencies, 0.95):>8} {cost:>9.4f} {drift_pct:>6.1f}%"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--http", default=None, help="URL base de un servicio real")
    parser.add_argument("--scenarios", default="growing,pivot,contradiction")
    parser.add_argument("--attachment-sizes", default="0,5,20,50,100")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--latency-budget-ms", type=int, default=8000)
    parser.add_argument("--cost-budget-usd", type=float, default=0.02)
    parser.add_argument("--max-total-usd", type=float, default=5.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    scenarios = [get_scenario(name.strip()) for name in args.scenarios.split(",")]
    sizes = [int(size) for size in args.attachment_sizes.split(",")]
    for size_kb in sizes:  # falla antes de gastar si falta un PDF
        attachment_files(size_kb)

    latency_metric = LatencyBudgetMetric(args.latency_budget_ms)
    cost_metric = CostBudgetMetric(args.cost_budget_usd)
    total_turns = sum(len(s.turns) for s in scenarios) * len(sizes) * args.repeats
    print(
        f"Stress run: {len(scenarios)} escenarios × {len(sizes)} tamaños × "
        f"{args.repeats} repeticiones = {total_turns} turnos "
        f"(tope USD {args.max_total_usd:.2f})"
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    spent = [0.0]
    rows = 0
    start = time.perf_counter()
    with args.output.open("w", newline="") as fh, open_client(args.http) as client:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        try:
            for scenario in scenarios:
                for size_kb in sizes:
                    for repeat in range(args.repeats):
                        print(
                            f"  → {scenario.name:<14} kb={size_kb:>3} "
                            f"repeat={repeat + 1}/{args.repeats}  (USD {spent[0]:.2f})"
                        )
                        rows += run_session(
                            client,
                            scenario,
                            size_kb,
                            repeat,
                            latency_metric,
                            cost_metric,
                            writer,
                            spent,
                            args.max_total_usd,
                        )
                        fh.flush()
        except BudgetExceeded as exc:
            print(f"\nCorrida cortada: {exc}")

    elapsed = int(time.perf_counter() - start)
    print(f"\n{rows} filas en {args.output} en {elapsed}s · gasto USD {spent[0]:.4f}")
    print_summary(args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
