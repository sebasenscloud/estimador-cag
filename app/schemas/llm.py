from pydantic import BaseModel


class LLMCallMetrics(BaseModel):
    """Observabilidad de una llamada al LLM: qué modelo, cuánto tardó y cuánto costó."""

    purpose: str  # estimation, metadata_extraction, ...
    model: str
    provider: str
    latency_ms: int
    tokens_in: int | None = None
    tokens_out: int | None = None
    # None si el modelo no está en MODEL_COSTS o el proveedor no informó tokens.
    cost_usd: float | None = None
