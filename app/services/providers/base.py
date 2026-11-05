from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

REFUSAL_MESSAGE = (
    "El modelo no pudo generar la estimación sobre la transcripción enviada."
)


class LLMProviderError(Exception):
    """Falla al comunicarse con el proveedor de LLM (red, API key, límites, etc.)."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int


class LLMProvider(ABC):
    """Interfaz común que deben cumplir todos los proveedores de LLM."""

    name: str

    def __init__(self, model: str, max_tokens: int):
        self.model = model
        self.max_tokens = max_tokens

    @abstractmethod
    def generate(self, system_prompt: str, user_message: str) -> LLMResult:
        """Devuelve la respuesta completa del modelo."""

    @abstractmethod
    def stream(
        self, system_prompt: str, user_message: str, usage: dict
    ) -> Iterator[str]:
        """Entrega la respuesta de a pedazos y, al terminar, completa `usage`
        con las claves model, input_tokens y output_tokens."""
