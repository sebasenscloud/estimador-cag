import hashlib
from collections import OrderedDict

from app.services.providers.base import LLMResult


class ExactMatchCache:
    """Caché en memoria: solo acierta si el pedido es idéntico, carácter por carácter.

    Cuando se llena, descarta la entrada usada hace más tiempo (LRU).
    """

    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._entries: OrderedDict[str, LLMResult] = OrderedDict()

    @staticmethod
    def make_key(provider: str, model: str, system_prompt: str, transcript: str) -> str:
        raw_key = f"{provider}\x00{model}\x00{system_prompt}\x00{transcript}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> LLMResult | None:
        result = self._entries.get(key)
        if result is not None:
            self._entries.move_to_end(key)
        return result

    def set(self, key: str, result: LLMResult) -> None:
        self._entries[key] = result
        self._entries.move_to_end(key)
        if len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
