"""Guardrail de entrada: tres capas que corren ANTES de llamar al LLM.

Basado en app/guardrails/input.py del curso (rama session_05_live).

1. Moderación (Moderation API de OpenAI vía LiteLLM): odio, violencia,
   contenido sexual... Solo corre si MODERATION_ENABLED y hay OPENAI_API_KEY.
   Si la llamada falla, deja pasar (fail open) y lo loguea: una caída del
   proveedor de moderación no puede tumbar el estimador.
2. Prompt injection (regex sobre patrones conocidos): segunda línea, barata,
   contra "ignore previous instructions". Patrones del curso + equivalentes en
   español.
3. PII (regex de emails, IBAN y teléfonos): no es exhaustivo a propósito; es
   el patrón, no un redactor apto para compliance.

Política: EXCEPTION, nunca fix-and-retry. Las tres lanzan
InputGuardrailViolation con un `reason` para que la capa HTTP devuelva 400 con
{reason, message} y el cliente muestre un mensaje acorde.

Corre antes del cache: un pedido malicioso o con datos personales no debe
quedar guardado ni servirse desde el cache.
"""

import re
from typing import Literal

import litellm
import structlog

from app.config import settings

logger = structlog.get_logger()

Reason = Literal["moderation", "prompt_injection", "pii"]


class InputGuardrailViolation(Exception):
    def __init__(self, message: str, *, reason: Reason) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


def guardrail_detail(error: InputGuardrailViolation) -> dict[str, str]:
    """Cuerpo del 400: {reason, message}, igual en todos los endpoints."""
    logger.info("estimate_blocked_by_input_guardrail", reason=error.reason)
    return {"reason": error.reason, "message": error.message}


_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        # Del curso (inglés); "ignore all previous" también se atrapa.
        r"ignore\s+(all\s+)?(previous|prior|all|the)\s+(instructions?|prompts?|rules?)",
        r"</?\s*(system|instructions?|prompt)\s*>",
        r"new\s+instructions?\s*[:.\-]",
        r"forget\s+(everything|all|previous)",
        r"\byou\s+are\s+now\b",
        r"\bdisregard\b.{0,40}\b(instructions?|prompts?|rules?|context|previous|prior)",
        # Equivalentes en español.
        r"ignor[aáe]\w*\s+(las\s+|todas\s+las\s+)?(instrucciones|reglas|indicaciones)",
        r"olvid[aáe]\w*\s+(todo|lo\s+anterior|las\s+instrucciones)",
        r"nuevas?\s+instrucci[oó]n(es)?\s*[:.\-]",
        r"\ba\s+partir\s+de\s+ahora\s+(sos|eres)\b",
    )
)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IBAN_RE = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{10,30}\b")
# Internacional (+XX) o 10+ dígitos seguidos: conservador para no marcar
# montos, fechas ni versiones.
_PHONE_RE = re.compile(r"(?:\+\d{1,3}[\s.-]?)?(?:\d[\s.-]?){9,12}\d")


def check_input(text: str) -> None:
    """Corre las tres capas en orden y lanza en la primera violación."""
    if settings.moderation_enabled and settings.openai_api_key:
        _check_moderation(text)
    _check_prompt_injection(text)
    _check_pii(text)


def _check_moderation(text: str) -> None:
    try:
        response = litellm.moderation(
            input=text,
            model=settings.moderation_model,
            api_key=settings.openai_api_key,
        )
    except Exception as error:  # noqa: BLE001 — fail open, con log
        logger.warning(
            "moderation_call_failed",
            error_type=type(error).__name__,
            error=str(error)[:200],
        )
        return

    result = response.results[0]
    if getattr(result, "flagged", False):
        categories = _flagged_categories(result)
        logger.info("moderation_flagged", categories=categories)
        raise InputGuardrailViolation(
            f"El texto fue marcado por moderación: {', '.join(categories) or 'sin detalle'}",
            reason="moderation",
        )


def _flagged_categories(result) -> list[str]:
    categories = getattr(result, "categories", None)
    if categories is None:
        return []
    data = (
        categories.model_dump()
        if hasattr(categories, "model_dump")
        else vars(categories)
    )
    return [name for name, flagged in data.items() if flagged]


def _check_prompt_injection(text: str) -> None:
    for pattern in _PROMPT_INJECTION_PATTERNS:
        match = pattern.search(text)
        if match:
            logger.info("prompt_injection_detected", match=match.group(0)[:80])
            raise InputGuardrailViolation(
                f"Se detectó texto con forma de instrucción: {match.group(0)[:80]!r}",
                reason="prompt_injection",
            )


def _check_pii(text: str) -> None:
    for regex, label in (
        (_EMAIL_RE, "un email"),
        (_IBAN_RE, "un IBAN"),
        (_PHONE_RE, "un teléfono"),
    ):
        if regex.search(text):
            logger.info("pii_detected", kind=label)
            raise InputGuardrailViolation(
                f"Se detectó {label} en la descripción: quitá los datos personales.",
                reason="pii",
            )
