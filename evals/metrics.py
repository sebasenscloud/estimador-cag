"""Métricas deterministas sobre un par (GoldenCase, EstimationResult).

Portadas de evals/metrics.py del curso (rama session_05_live). Viven en el
repo, en vez de delegar en un juez LLM, para que la suite corra sin LLM y diga
exactamente por qué falló un caso. Las tres son binarias: score 1.0 o 0.0.

Diferencia con el curso: allí un caso fuera de scope espera el "sobre vacío"
(costo 0, 1 semana). Nuestro contrato (prompt v1/v2) pide en cambio el prefijo
"Out of scope:" más una única fase de relevamiento, así que cost_bounds
verifica eso.
"""

from dataclasses import dataclass

from app.schemas import EstimationResult
from app.schemas.estimation import LOW_CONFIDENCE_PCT, OUT_OF_SCOPE_PREFIX
from evals.dataset import GoldenCase


@dataclass
class MetricResult:
    name: str
    score: float
    passed: bool
    details: str


def _result(name: str, problems: list[str], ok: str) -> MetricResult:
    return MetricResult(
        name=name,
        score=0.0 if problems else 1.0,
        passed=not problems,
        details="; ".join(problems) or ok,
    )


def _outside(value: int, bounds: tuple[int, int] | None) -> bool:
    return bounds is not None and not bounds[0] <= value <= bounds[1]


class SchemaAdherenceMetric:
    """Instructor y los validadores ya garantizan el schema; esta métrica
    detecta si alguna de esas garantías se pierde (por ejemplo, un cambio que
    borra un validador). También revisa la cantidad de fases contra el rango."""

    name = "schema_adherence"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        problems: list[str] = []

        phase_sum = sum(phase.cost_eur for phase in result.phases)
        if phase_sum != result.total_cost_eur:
            problems.append(
                f"phases sum ({phase_sum}) != total_cost_eur ({result.total_cost_eur})"
            )
        if result.confidence_pct < LOW_CONFIDENCE_PCT and not (
            result.summary.startswith(OUT_OF_SCOPE_PREFIX)
        ):
            problems.append(f"low confidence missing '{OUT_OF_SCOPE_PREFIX}' prefix")
        if _outside(len(result.phases), case.expected_phase_count_range):
            lo, hi = case.expected_phase_count_range
            problems.append(f"phase count {len(result.phases)} outside [{lo}, {hi}]")

        return _result(self.name, problems, "schema ok")


class CostBoundsMetric:
    """Costo y duración dentro del rango esperado. Un caso fuera de scope
    tiene que salir declarado como tal (ver docstring del módulo)."""

    name = "cost_bounds"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        problems: list[str] = []

        if case.expected_out_of_scope:
            if not result.is_out_of_scope:
                problems.append(
                    f"expected out of scope, got an estimate of "
                    f"{result.total_cost_eur} EUR / {result.total_duration_weeks}w"
                )
            elif len(result.phases) != 1:
                problems.append(
                    f"out of scope should have 1 discovery phase, got {len(result.phases)}"
                )
        else:
            if _outside(result.total_cost_eur, case.expected_cost_range_eur):
                lo, hi = case.expected_cost_range_eur
                problems.append(
                    f"cost {result.total_cost_eur} EUR outside [{lo}, {hi}]"
                )
            if _outside(
                result.total_duration_weeks, case.expected_duration_weeks_range
            ):
                lo, hi = case.expected_duration_weeks_range
                problems.append(
                    f"duration {result.total_duration_weeks}w outside [{lo}, {hi}]"
                )

        return _result(self.name, problems, "cost & duration within bounds")


class ContentRecallMetric:
    """Recall liviano: ¿el summary y las fases mencionan lo que le importaba al
    usuario? Los casos fuera de scope se saltan."""

    name = "content_recall"

    def evaluate(self, case: GoldenCase, result: EstimationResult) -> MetricResult:
        if case.expected_out_of_scope:
            return MetricResult(
                self.name, 1.0, True, "skipped — case is out-of-scope by design"
            )

        haystack = " ".join(
            [result.summary]
            + [f"{phase.name} {phase.description}" for phase in result.phases]
        ).lower()
        problems: list[str] = []

        missing = [t for t in case.expected_in_summary if t.lower() not in haystack]
        if missing:
            problems.append(f"missing in summary: {missing}")
        techs = case.expected_technologies_any_of
        if techs and not any(t.lower() in haystack for t in techs):
            problems.append(f"none of {techs} mentioned anywhere")

        return _result(self.name, problems, "expected content present")


_DEFAULT_METRICS = (SchemaAdherenceMetric(), CostBoundsMetric(), ContentRecallMetric())


def run_all_metrics(case: GoldenCase, result: EstimationResult) -> list[MetricResult]:
    return [metric.evaluate(case, result) for metric in _DEFAULT_METRICS]
