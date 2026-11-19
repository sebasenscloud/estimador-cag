"""Feedback estructurado del crítico (Actor-Critic-Boss).

El schema es EL contrato entre el crítico y el Boss: el Boss lee `category`,
`severity` y `field_path` de cada problema para decidir (aceptar, iterar o
sintetizar). Una crítica en texto libre obligaría a parsear prosa y empuja al
actor a justificarse en lugar de corregir (sesgo de confirmación). Con
`field_path` el actor sabe qué campo concreto rehacer.

Basado en app/schemas/critic.py del curso (rama session_05_live).
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CriticIssueCategory = Literal[
    "math_error",
    "hallucination",
    "scope_mismatch",
    "phase_imbalance",
    "missing_assumption",
    "unrealistic_estimate",
    "tier_mismatch",
]
CriticIssueSeverity = Literal["critical", "major", "minor"]
CriticVerdict = Literal["accept", "needs_iteration", "reject"]


class CriticIssue(BaseModel):
    """Un defecto. `field_path` apunta a un campo de la estimación revisada
    (`phases[2].cost_eur`, `summary`, `total_cost_eur`); el Boss lo pega tal
    cual en el siguiente prompt del actor."""

    category: CriticIssueCategory
    severity: CriticIssueSeverity
    field_path: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=5, max_length=500)
    suggested_fix: str | None = Field(default=None, max_length=300)


class CriticFeedback(BaseModel):
    """Salida del crítico. Iterar exige al menos un problema critical o major:
    por detalles menores no se gasta otra llamada al actor."""

    verdict: CriticVerdict
    issues: list[CriticIssue] = Field(default_factory=list, max_length=12)
    confidence_in_review: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def iteration_requires_blocking_issue(self) -> "CriticFeedback":
        if self.verdict == "needs_iteration" and not any(
            issue.severity in {"critical", "major"} for issue in self.issues
        ):
            raise ValueError(
                "verdict 'needs_iteration' exige al menos un issue critical o "
                "major; si solo hay minor, el veredicto es accept."
            )
        if self.verdict == "reject" and not self.issues:
            raise ValueError(
                "verdict 'reject' exige al menos un issue que lo explique."
            )
        return self


BossDecision = Literal["accept", "iterate", "synthesize"]


class ACBIteration(BaseModel):
    """Una ronda: qué dijo el crítico y qué decidió el Boss después."""

    iteration: int = Field(ge=0)
    decision_after: BossDecision
    critic_verdict: CriticVerdict
    critic_confidence: int = Field(ge=0, le=100)
    issue_summary: list[str] = Field(default_factory=list)


class BossTrace(BaseModel):
    """Traza completa del ACB: viaja en la respuesta para auditar el turno."""

    iterations: list[ACBIteration] = Field(default_factory=list)
    final_decision: BossDecision
    iterations_run: int = Field(ge=0)
