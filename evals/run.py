"""Runner: corre el golden dataset contra el servicio y puntúa cada caso.

Portado de evals/run.py del curso (rama session_05_live). Por defecto usa
TestClient, en el mismo proceso: no hace falta levantar el servidor. Con
--http BASE apunta a un servicio real.

    uv run python -m evals.run --mode actor --limit 8
    uv run python -m evals.run --mode acb --limit 8
    uv run python -m evals.run --mode actor --http http://127.0.0.1:8000

ATENCIÓN: llama al LLM real (cuesta plata). Cada caso usa una sesión nueva
para que no se contaminen entre sí.

Imprime una fila por caso y un resumen por métrica, y guarda el reporte en
evals/<mode>.json para poder hacer diff entre corridas.

Diferencias con el curso:

- Si el guardrail de entrada rechaza (400) un caso que espera fuera de scope,
  eso ES el resultado esperado: se sintetiza una respuesta con nuestro
  contrato (prefijo "Out of scope:" y una fase) para que las métricas lo
  puntúen. En el curso el sobre es costo 0 / 1 semana.
- Se registra si el tier resuelto coincide con expected_tier (informativo:
  no cuenta para pasar o fallar, igual que en el curso).
"""

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.main import app
from app.schemas import EstimationResult
from app.schemas.estimation import OUT_OF_SCOPE_PREFIX
from app.sessions import session_store
from evals.dataset import GoldenCase, load_dataset
from evals.metrics import MetricResult, run_all_metrics

API = "/api/v1/sessions"
EVALS_DIR = Path(__file__).resolve().parent


def _form_body(case: GoldenCase) -> dict[str, str]:
    return {
        "transcript": case.transcript,
        "project_type": case.project_type.value,
        "detail_level": case.detail_level.value,
        "output_format": case.output_format.value,
        # Como en el curso: "auto" no manda el campo; otro valor es override.
        **({"tier": case.tier} if case.tier != "auto" else {}),
    }


GUARDRAIL_REASONS = {"prompt_injection", "moderation", "pii"}


def guardrail_payload(reason: str, message: str) -> dict[str, Any]:
    """Respuesta sintética para un rechazo del guardrail de entrada."""
    return {
        "result": {
            "summary": f"{OUT_OF_SCOPE_PREFIX} rechazado por el guardrail de "
            f"entrada ({reason}): {message}"[:1200],
            "phases": [
                {
                    "name": "Bloqueado",
                    "description": "El guardrail de entrada no dejó llegar el pedido al LLM.",
                    "duration_weeks": 1,
                    "cost_eur": 0,
                    "confidence_pct": 0,
                }
            ],
            "total_duration_weeks": 1,
            "total_cost_eur": 0,
            "confidence_pct": 0,
        },
        "prompt_version": f"guardrail/{reason}",
    }


def _maybe_guardrail_payload(case: GoldenCase, response) -> dict[str, Any] | None:
    if response.status_code != 400 or not case.expected_out_of_scope:
        return None
    detail = response.json().get("detail")
    if not isinstance(detail, dict) or detail.get("reason") not in GUARDRAIL_REASONS:
        return None
    return guardrail_payload(detail["reason"], detail.get("message", ""))


def run_case(
    client: httpx.Client | TestClient, case: GoldenCase, mode: str
) -> tuple[dict[str, Any], int]:
    """Crea una sesión nueva y estima el caso. Devuelve (payload, latencia).

    En proceso, la sesión se borra del store al terminar. Contra un servidor
    real queda viva hasta que se reinicie (la API no expone DELETE).
    """
    session_id = client.post(API).json()["session_id"]
    try:
        start = time.perf_counter()
        endpoint = "estimate-acb" if mode == "acb" else "estimate"
        response = client.post(f"{API}/{session_id}/{endpoint}", data=_form_body(case))
        latency_ms = int((time.perf_counter() - start) * 1000)
        synthetic = _maybe_guardrail_payload(case, response)
        if synthetic is not None:
            return synthetic, latency_ms
        response.raise_for_status()
        return response.json(), latency_ms
    finally:
        if isinstance(client, TestClient):
            session_store.delete(session_id)


def evaluate(case: GoldenCase, payload: dict[str, Any]) -> list[MetricResult]:
    return run_all_metrics(case, EstimationResult.model_validate(payload["result"]))


def _format_row(case_id: str, latency_ms: int, metrics: list[MetricResult]) -> str:
    parts = [f"{case_id:<34}", f"{latency_ms:>6} ms"]
    parts += [f"{m.name}={'PASS' if m.passed else 'FAIL'}" for m in metrics]
    return " | ".join(parts)


def run(
    cases: list[GoldenCase], mode: str, client: httpx.Client | TestClient
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    totals: dict[str, list[bool]] = {}
    print(f"Running {len(cases)} cases against mode={mode}")

    for case in cases:
        try:
            payload, latency_ms = run_case(client, case, mode)
        except Exception as exc:  # noqa: BLE001
            # Un caso roto (502, timeout) no corta la corrida.
            print(f"{case.id}: ERROR {type(exc).__name__}: {str(exc)[:200]}")
            rows.append({"id": case.id, "passed": False, "error": str(exc)[:400]})
            continue

        metrics = evaluate(case, payload)
        print(_format_row(case.id, latency_ms, metrics))
        for metric in metrics:
            totals.setdefault(metric.name, []).append(metric.passed)
        rows.append(
            {
                "id": case.id,
                "latency_ms": latency_ms,
                "passed": all(m.passed for m in metrics),
                "metrics": [m.__dict__ for m in metrics],
                "result_summary": payload["result"]["summary"][:200],
                "prompt_version": payload.get("prompt_version"),
                "tier": payload.get("tier"),
                "tier_rule": payload.get("tier_rule"),
                "expected_tier": case.expected_tier,
                "acb_final_decision": (payload.get("acb") or {}).get("final_decision"),
                "acb_iterations": (payload.get("acb") or {}).get("iterations_run"),
                "llm_calls": len(payload.get("llm_calls", [])),
                "cost_usd": sum(
                    call.get("cost_usd") or 0 for call in payload.get("llm_calls", [])
                ),
            }
        )

    passing = sum(row["passed"] for row in rows)
    print(f"\nSummary\n  {passing}/{len(cases)} cases passing all metrics")
    for name, results in totals.items():
        print(f"  {name}: {sum(results)}/{len(results)}")
    return {
        "mode": mode,
        "cases_total": len(cases),
        "cases_passing": passing,
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("actor", "acb"), default="actor")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--http", default=None, help="URL base, ej. http://127.0.0.1:8000"
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    cases = load_dataset()[: args.limit]
    if args.http:
        with httpx.Client(base_url=args.http, timeout=300.0) as client:
            report = run(cases, args.mode, client)
    else:
        with TestClient(app) as client:
            report = run(cases, args.mode, client)

    output = args.output or EVALS_DIR / f"{args.mode}.json"
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"\nReport written to {output}")
    return 0 if report["cases_passing"] == report["cases_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
