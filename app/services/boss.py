"""Boss del Actor-Critic-Boss: decide qué hacer con la auditoría.

Un autómata chico sobre el veredicto del crítico y el presupuesto de rondas:

| Crítico dice    | Rondas    | Boss decide | Qué pasa                          |
|-----------------|-----------|-------------|-----------------------------------|
| accept          | —         | accept      | devuelve el borrador              |
| reject          | —         | synthesize  | no tiene arreglo con este actor   |
| needs_iteration | quedan    | iterate     | vuelve al actor con el feedback   |
| needs_iteration | no quedan | synthesize  | lo intentó y no convergió         |

El Boss no llama al LLM: solo elige el próximo paso. Recibe FUNCIONES (actor y
crítico), no objetos: en los tests se inyectan falsos sin mocks complicados.
No recuerda nada entre estimaciones (stateless).

Resuelve los dos fallos del patrón:
- Bucle infinito: el crítico siempre encuentra algo; el Boss pone el límite
  (BOSS_MAX_ITERATIONS) y el plan B (synthesize).
- Sesgo de confirmación: el feedback es estructurado (CriticFeedback), así
  que el actor sabe qué campo rehacer en lugar de justificarse.

Synthesize no tira el trabajo: devuelve el último borrador con las
advertencias del crítico delante y la confianza a la mitad, con piso en
LOW_CONFIDENCE_PCT para no caer en "Out of scope". La honestidad viaja en
BossTrace.final_decision.

Basado en app/services/boss.py del curso (rama session_05_live).
"""

from collections.abc import Callable

import structlog

from app.schemas import EstimationResult
from app.schemas.critic import ACBIteration, BossDecision, BossTrace, CriticFeedback
from app.schemas.estimation import LOW_CONFIDENCE_PCT

logger = structlog.get_logger()

ActorCallable = Callable[[CriticFeedback | None], EstimationResult]
CriticCallable = Callable[[EstimationResult], CriticFeedback]

CAVEATS_HEADER = "⚠ Advertencias abiertas de la revisión independiente"


def run(
    actor: ActorCallable, critic: CriticCallable, max_iterations: int
) -> tuple[EstimationResult, BossTrace]:
    if max_iterations < 1:
        raise ValueError("max_iterations tiene que ser >= 1")

    trace = BossTrace(final_decision="accept", iterations_run=0)
    feedback: CriticFeedback | None = None

    for iteration in range(max_iterations):
        draft = actor(feedback)
        review = critic(draft)
        decision = decide(review, iterations_left=max_iterations - iteration - 1)

        trace.iterations.append(
            ACBIteration(
                iteration=iteration,
                decision_after=decision,
                critic_verdict=review.verdict,
                critic_confidence=review.confidence_in_review,
                issue_summary=[
                    f"[{issue.severity}] {issue.category} @ {issue.field_path}"
                    for issue in review.issues[:5]
                ],
            )
        )
        trace.iterations_run = iteration + 1
        logger.info(
            "boss_iteration",
            iteration=iteration,
            decision=decision,
            verdict=review.verdict,
            issues=len(review.issues),
        )

        if decision == "accept":
            return draft, trace
        if decision == "synthesize":
            trace.final_decision = "synthesize"
            return synthesize(draft, review), trace
        feedback = review  # iterate

    raise AssertionError("inalcanzable: la última ronda siempre acepta o sintetiza")


def decide(review: CriticFeedback, iterations_left: int) -> BossDecision:
    if review.verdict == "accept":
        return "accept"
    if review.verdict == "reject" or iterations_left == 0:
        return "synthesize"
    return "iterate"


def synthesize(draft: EstimationResult, feedback: CriticFeedback) -> EstimationResult:
    """Último borrador + advertencias del crítico delante + confianza a la mitad."""
    lines = [
        f"- [{issue.severity}] {issue.category} ({issue.field_path}): {issue.description}"
        for issue in feedback.issues
    ] or ["- (sin detalle)"]
    caveats = f"{CAVEATS_HEADER} (el ciclo no convergió):\n" + "\n".join(lines)

    if draft.is_out_of_scope:
        # El prefijo "Out of scope:" tiene que quedar primero: las advertencias
        # van detrás y la confianza (ya baja) no se toca.
        return draft.model_copy(update={"summary": f"{draft.summary}\n\n{caveats}"})
    return draft.model_copy(
        update={
            "summary": f"{caveats}\n\n{draft.summary}",
            # Piso en el umbral: por debajo, el contrato exige "Out of scope:",
            # y eso contradice devolver un borrador utilizable.
            "confidence_pct": max(LOW_CONFIDENCE_PCT, draft.confidence_pct // 2),
        }
    )
