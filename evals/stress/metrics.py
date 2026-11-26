"""Tres métricas del stress test, deterministas como las del golden dataset.

Portadas de evals/stress/metrics.py del curso. Viven aparte de evals.metrics
porque no evalúan (caso, estimación) sino la telemetría de un turno
(TurnObservation) y el snapshot de la sesión (GET /sessions/{id}). Reusan
MetricResult para que los reportes tengan la misma forma.

Sin embeddings ni juez LLM: MemoryDriftMetric busca el dato como texto
(sin distinguir mayúsculas). Alcanza para la pregunta del ejercicio ("en el
turno N, ¿sigue vivo el dato del turno 1?") y no tiene fallas propias: si
dice que el dato no está, no está.
"""

import json
from typing import Any, Literal

from app.schemas import TurnObservation
from evals.metrics import MetricResult

FactField = Literal["project_name", "technologies", "scope", "summary", "any"]

# Partes del snapshot que NO son memoria del modelo: el log de cambios de la
# metadata guarda los valores viejos y haría pasar el drift aunque el dato ya
# no llegue al prompt.
_NOT_MEMORY = ("metadata_log", "last_llm_calls")


def _result(name: str, passed: bool, details: str) -> MetricResult:
    return MetricResult(name, 1.0 if passed else 0.0, passed, details)


class LatencyBudgetMetric:
    """Latencia del turno contra un presupuesto (un SLA, por ejemplo 8000 ms)."""

    name = "latency_budget"

    def __init__(self, budget_ms: int) -> None:
        if budget_ms <= 0:
            raise ValueError("budget_ms tiene que ser positivo")
        self.budget_ms = budget_ms

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        passed = observation.latency_ms <= self.budget_ms
        details = f"{observation.latency_ms} ms vs budget {self.budget_ms} ms"
        return _result(self.name, passed, details)


class CostBudgetMetric:
    """Costo de UN turno contra un tope en USD. El acumulado de la sesión lo
    calcula el runner con las observaciones de cada turno."""

    name = "cost_budget"

    def __init__(self, budget_usd: float) -> None:
        if budget_usd <= 0:
            raise ValueError("budget_usd tiene que ser positivo")
        self.budget_usd = budget_usd

    def evaluate(self, observation: TurnObservation) -> MetricResult:
        cost = observation.cost_usd
        if cost is None:
            return _result(self.name, False, "costo desconocido (modelo sin precio)")
        passed = cost <= self.budget_usd
        return _result(
            self.name, passed, f"${cost:.6f} vs budget ${self.budget_usd:.6f}"
        )


class MemoryDriftMetric:
    """¿Un dato de un turno anterior sigue apareciendo en la sesión?

    `fact_field` acota dónde buscar:

    - project_name: solo project_metadata.project_name.
    - technologies: solo project_metadata.mentioned_technologies.
    - scope: solo project_metadata.agreed_scope.
    - summary: solo el resumen acumulado.
    - any: todo lo que llega al prompt (metadata, resumen, tier...), sin el
      log de cambios ni las llamadas.
    """

    name = "memory_drift"

    def __init__(self, fact: str, fact_field: FactField = "any") -> None:
        if not fact:
            raise ValueError("fact no puede estar vacío")
        self.fact = fact
        self.fact_field = fact_field

    def evaluate(self, snapshot: dict[str, Any]) -> MetricResult:
        found = self.fact.lower() in self._haystack(snapshot).lower()
        details = (
            f"fact={self.fact!r} field={self.fact_field} "
            f"{'present' if found else 'missing'}"
        )
        return _result(self.name, found, details)

    def _haystack(self, snapshot: dict[str, Any]) -> str:
        metadata = snapshot.get("project_metadata") or {}
        if self.fact_field == "project_name":
            return str(metadata.get("project_name") or "")
        if self.fact_field == "technologies":
            return " ".join(metadata.get("mentioned_technologies") or [])
        if self.fact_field == "scope":
            return str(metadata.get("agreed_scope") or "")
        if self.fact_field == "summary":
            return str(snapshot.get("summary") or "")
        memory = {k: v for k, v in snapshot.items() if k not in _NOT_MEMORY}
        return json.dumps(memory, default=str, ensure_ascii=False)
