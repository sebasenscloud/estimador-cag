"""Decisión arquitectónica: ¿CAG, RAG o híbrido? (paso 2.1, «Article 1»).

Es la forma ejecutable de la decisión. NO forma parte de la API: como un
script de análisis, se corre para razonar sobre el corpus antes de ingerir
nada:

    uv run python -m app.ingestion.architecture
    uv run python -m app.ingestion.architecture --model gpt-4o-mini

Dos piezas, portadas de app/ingestion/architecture.py del curso:

- CAGViability: las cuatro RESTRICCIONES del CAG (ventana de contexto, costo,
  latencia, lost in the middle). `viable` es un all([...]) a propósito: las
  cuatro son un AND, no un OR. Una sola en rojo mata el CAG.
- recommend_architecture: evalúa los cuatro EJES de decisión (volumen,
  frecuencia de refresco, trazabilidad, control de acceso) y devuelve
  "CAG", "Hybrid" o "RAG".

El fine-tuning no está a propósito: es una capa ortogonal. Enseña CÓMO
responder; no aporta datos nuevos ni resuelve la trazabilidad, y nunca
reemplaza a un retrieval mal diseñado.

Heurísticas simples a propósito: el objetivo es que la decisión se pueda
explicar a un Director de Producto, no un modelo de costos calibrado.
"""

import argparse
from dataclasses import dataclass
from typing import Literal

Architecture = Literal["CAG", "Hybrid", "RAG"]

# Por debajo de estos tokens asumimos que el modelo todavía "usa el centro"
# del contexto y la latencia queda bajo un SLO razonable. Por encima, muerden
# lost in the middle y la latencia.
CENTER_RECALL_TOKEN_BUDGET = 100_000
# Un corpus entra en CAG solo si deja holgura en la ventana (usamos el 70%):
# el resto es para system prompt, historial y respuesta.
USABLE_WINDOW_FRACTION = 0.7
# El caché de prefijo solo compensa si el corpus es estable al menos una semana.
CACHE_FRIENDLY_REFRESH_DAYS = 7


@dataclass
class CAGViability:
    """Las cuatro restricciones del CAG. `viable` es un AND de las cuatro."""

    context_window_ok: bool  # ¿cabe el corpus completo en la ventana?
    cost_ok: bool  # ¿el costo por turno, con caché de prefijo, es asumible?
    latency_ok: bool  # ¿la latencia con el corpus completo queda bajo el SLO?
    lost_in_the_middle_ok: bool  # ¿el modelo USA lo que queda en el centro?

    @property
    def viable(self) -> bool:
        return all(
            [
                self.context_window_ok,
                self.cost_ok,
                self.latency_ok,
                self.lost_in_the_middle_ok,
            ]
        )

    def failing_constraints(self) -> list[str]:
        """Restricciones en rojo; vacía si y solo si `viable`."""
        return [
            name
            for name, ok in (
                ("context_window", self.context_window_ok),
                ("cost", self.cost_ok),
                ("latency", self.latency_ok),
                ("lost_in_the_middle", self.lost_in_the_middle_ok),
            )
            if not ok
        ]


@dataclass
class CorpusProfile:
    name: str
    estimated_tokens: int  # volumen total estimado
    refresh_frequency_days: float  # cada cuánto cambian los datos
    traceability_required: bool  # ¿hay que poder citar la fuente?
    access_control_required: bool  # ¿hay que filtrar por permisos de usuario?


@dataclass
class ModelProfile:
    name: str
    context_window: int  # tokens
    cost_per_1k_input: float  # USD
    prefix_caching: bool  # ¿soporta caché de prefijo?


def fits_in_window(corpus: CorpusProfile, model: ModelProfile) -> bool:
    return corpus.estimated_tokens <= model.context_window * USABLE_WINDOW_FRACTION


def assess_cag_viability(corpus: CorpusProfile, model: ModelProfile) -> CAGViability:
    """Deriva las cuatro restricciones a partir del corpus y del modelo."""
    within_center_budget = corpus.estimated_tokens <= CENTER_RECALL_TOKEN_BUDGET
    return CAGViability(
        context_window_ok=fits_in_window(corpus, model),
        cost_ok=model.prefix_caching
        and corpus.refresh_frequency_days >= CACHE_FRIENDLY_REFRESH_DAYS,
        latency_ok=within_center_budget,
        lost_in_the_middle_ok=within_center_budget,
    )


def recommend_architecture(corpus: CorpusProfile, model: ModelProfile) -> Architecture:
    """Recomienda una arquitectura según los cuatro ejes de decisión."""
    # Eje 1 · VOLUMEN: ¿cabe con holgura en la ventana?
    fits = fits_in_window(corpus, model)
    # Eje 2 · FRECUENCIA: cambios más que semanales rompen el caché de prefijo.
    cache_friendly = corpus.refresh_frequency_days >= CACHE_FRIENDLY_REFRESH_DAYS
    # Eje 3 · TRAZABILIDAD: sin retriever no hay metadata de fuente que citar.
    traceability_doable = not corpus.traceability_required
    # Eje 4 · CONTROL DE ACCESO: el CAG no filtra por usuario antes del prompt.
    access_control_doable = not corpus.access_control_required

    if all([fits, cache_friendly, traceability_doable, access_control_doable]):
        return "CAG"
    # Cabe y es estable, pero hay que citar o filtrar: lo estable por CAG y lo
    # volátil o sensible por RAG.
    if fits and cache_friendly:
        return "Hybrid"
    return "RAG"


# --- El Proyecto 2 del curso, con números defendibles -------------------------

PROYECTO_2 = CorpusProfile(
    name="Proyecto 2",
    estimated_tokens=250_000,  # presupuestos + transcripciones + tarifas + adendas
    refresh_frequency_days=7,  # cierre comercial semanal
    traceability_required=True,  # legal exige citar la fuente
    access_control_required=True,  # información confidencial del cliente
)

MODELS = {
    # El del curso.
    "gpt-4o-mini": ModelProfile(
        name="gpt-4o-mini",
        context_window=128_000,
        cost_per_1k_input=0.00015,
        prefix_caching=True,
    ),
    # El nuestro (LLM_MODEL): ventana de 200K, USD 1 por millón de tokens de
    # entrada y prompt caching de Anthropic.
    "claude-haiku-4-5": ModelProfile(
        name="claude-haiku-4-5",
        context_window=200_000,
        cost_per_1k_input=0.001,
        prefix_caching=True,
    ),
}
CURRENT_MODEL = MODELS["claude-haiku-4-5"]


def report(corpus: CorpusProfile, model: ModelProfile) -> str:
    viability = assess_cag_viability(corpus, model)
    lines = [
        (
            f"Corpus '{corpus.name}': {corpus.estimated_tokens:,} tokens, "
            f"refresco cada {corpus.refresh_frequency_days:g} días, "
            f"trazabilidad={corpus.traceability_required}, "
            f"control_acceso={corpus.access_control_required}"
        ),
        (
            f"Modelo '{model.name}': ventana {model.context_window:,} tokens "
            f"(útil {int(model.context_window * USABLE_WINDOW_FRACTION):,}), "
            f"prefix_caching={model.prefix_caching}"
        ),
        "",
    ]
    if viability.viable:
        lines.append("Viabilidad CAG: viable (las 4 restricciones en verde)")
    else:
        lines.append("Viabilidad CAG: NO viable")
        lines.append(
            f"  Restricciones en rojo: {', '.join(viability.failing_constraints())}"
        )
    lines.append(
        f"Recomendación arquitectónica: {recommend_architecture(corpus, model)}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="¿CAG, RAG o híbrido?")
    parser.add_argument("--model", choices=sorted(MODELS), default=CURRENT_MODEL.name)
    args = parser.parse_args(argv)
    print(report(PROYECTO_2, MODELS[args.model]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
