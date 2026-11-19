"""Tier dinámico: a qué audiencia va dirigida la estimación en cada turno.

La misma estimación se presenta distinto según quién la lee (bloque <audience>
de app/prompts/estimation/v2/system.j2):

- executive: sponsor o dirección. Cifra defendible, riesgos primero, sin jerga.
- pm: jefe de proyecto. Hitos, quién entrega qué, confianza prudente.
- developer: tech lead. Stack, integraciones, riesgo técnico por fase.
- default: audiencia mixta, sin ajustes.

Estático vs. dinámico: si el cliente fija el tier, falla en conversaciones
largas, donde la audiencia cambia (empieza el comercial y termina el CTO). Por
eso el tier se deduce en cada turno a partir de pistas en el texto del turno y
en el project_metadata (agreed_scope y mentioned_technologies, como en el
curso). No mira las anclas: un NDA del turno 1 sigue pesando en los turnos
siguientes solo si el extractor lo dejó en el agreed_scope.

Estructura del curso (tier_resolver.py): un enum Tier, un ResolutionContext con
lo que se mira en el turno (transcript, metadata y un override opcional) y una
lista de TierRule(name, tier, predicate). Gana la primera regla cuyo predicado
da True:

0. explicit_override    → el tier que pasa quien llama.
1. nda_detected         → executive: hay confidencialidad en juego.
2. regulatory_context   → executive: una normativa es una decisión de dirección.
3. technical_audience   → developer: al menos dos términos técnicos distintos.
4. low_budget_pm        → pm: equipo de 1-2 personas (proyecto chico).
5. sin regla            → default (rule "default").

Las regex en inglés son las del curso; cada una suma su equivalente en español.
Los umbrales (2 términos, equipo <= 2) son los del curso y se pueden ajustar
con TIER_TECHNICAL_TERMS y TIER_SMALL_TEAM_SIZE.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

import structlog

from app.config import settings
from app.sessions import ProjectMetadata

logger = structlog.get_logger()


class Tier(str, Enum):
    EXECUTIVE = "executive"
    PM = "pm"
    DEVELOPER = "developer"
    DEFAULT = "default"


TIERS = tuple(tier.value for tier in Tier)


@dataclass
class ResolutionContext:
    transcript: str
    metadata: ProjectMetadata
    override: Tier | None = None


@dataclass
class TierRule:
    name: str
    tier: Tier
    predicate: Callable[[ResolutionContext], bool]


@dataclass(frozen=True)
class TierResolution:
    tier: str
    rule: str


# --- Predicados --------------------------------------------------------------

_NDA_PATTERN = re.compile(
    r"\b(nda|non[- ]?disclosure|confidential|under embargo|legal hold)\b"
    r"|\b(confidencial|acuerdo de confidencialidad|bajo embargo)\b",
    re.IGNORECASE,
)
_REGULATORY_PATTERN = re.compile(
    r"\b(hipaa|gdpr|rgpd|sox|pci[- ]?dss|fda|iso[- ]?27001|ccpa)\b",
    re.IGNORECASE,
)
# Las del curso (infraestructura) más términos de stack y arquitectura habituales
# en una charla con un tech lead, en inglés y en español.
_DEV_KEYWORDS_PATTERN = re.compile(
    r"\b(docker|kubernetes|k8s|microservice|microservices|terraform|iac"
    r"|helm|grpc|graphql|kafka|airflow|spark|rabbitmq"
    r"|microservicios?|api|apis|rest|webhooks?|sdk|backend|frontend|ci/cd"
    r"|serverless|lambda|redis|postgres\w*|mysql|mongodb|elasticsearch"
    r"|react|angular|vue|next\.js|django|fastapi|rails|spring|node(\.js)?"
    r"|flutter|swift|kotlin|aws|gcp|azure|oauth|sso|jwt|latencia|latency"
    r"|escalabilidad|scalability|throughput|cach[eé]|queue|cola de mensajes"
    r"|migraci[oó]n de datos|data migration|etl|esquema|schema)\b",
    re.IGNORECASE,
)


def technical_terms(text: str) -> set[str]:
    return {m.group(0).lower() for m in _DEV_KEYWORDS_PATTERN.finditer(text)}


def _has_nda(context: ResolutionContext) -> bool:
    """El NDA puede estar en el turno o haber quedado en el alcance acordado."""
    if _NDA_PATTERN.search(context.transcript):
        return True
    scope = context.metadata.agreed_scope
    return bool(scope and _NDA_PATTERN.search(scope))


def _has_regulatory_context(context: ResolutionContext) -> bool:
    """La normativa puede estar en el turno, en el alcance o entre las tecnologías."""
    if _REGULATORY_PATTERN.search(context.transcript):
        return True
    scope = context.metadata.agreed_scope
    if scope and _REGULATORY_PATTERN.search(scope):
        return True
    return any(
        _REGULATORY_PATTERN.search(tech)
        for tech in context.metadata.mentioned_technologies
    )


def _is_small_team(context: ResolutionContext) -> bool:
    size = context.metadata.assumed_team_size
    return size is not None and size <= settings.tier_small_team_size


def _technical_audience(context: ResolutionContext) -> bool:
    # Como en el curso: al menos dos términos técnicos distintos; una sola
    # mención ("corremos en docker") es demasiado ruido para cambiar el tier.
    return len(technical_terms(context.transcript)) >= settings.tier_technical_terms


# --- Cadena de reglas --------------------------------------------------------

_RULES: tuple[TierRule, ...] = (
    TierRule("nda_detected", Tier.EXECUTIVE, _has_nda),
    TierRule("regulatory_context", Tier.EXECUTIVE, _has_regulatory_context),
    TierRule("technical_audience", Tier.DEVELOPER, _technical_audience),
    TierRule("low_budget_pm", Tier.PM, _is_small_team),
)


def resolve_tier(
    text: str, metadata: ProjectMetadata, override: Tier | None = None
) -> TierResolution:
    """Devuelve el tier del turno y la regla que lo eligió.

    Precedencia:
        1. override (lo pasa quien llama) gana.
        2. Si no, la cadena de reglas en orden: gana la primera que da True.
        3. Si no, DEFAULT.
    """
    context = ResolutionContext(text, metadata, override)

    if override is not None:
        logger.info("tier_resolved", tier=override.value, rule="explicit_override")
        return TierResolution(override.value, "explicit_override")

    for rule in _RULES:
        try:
            if rule.predicate(context):
                logger.info("tier_resolved", tier=rule.tier.value, rule=rule.name)
                return TierResolution(rule.tier.value, rule.name)
        except Exception as exc:  # noqa: BLE001
            # Un predicado roto no tumba el turno: se salta y sigue la cadena.
            logger.warning(
                "tier_rule_predicate_failed",
                rule=rule.name,
                error_type=type(exc).__name__,
                error=str(exc)[:120],
            )

    logger.info("tier_resolved", tier=Tier.DEFAULT.value, rule="default")
    return TierResolution(Tier.DEFAULT.value, "default")
