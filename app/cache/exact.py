import hashlib
from collections import OrderedDict

from pydantic import BaseModel


class ExactMatchCache:
    """Caché en memoria: solo acierta si la clave es idéntica, carácter por carácter.

    Cuando se llena, descarta la entrada usada hace más tiempo (LRU).
    """

    def __init__(self, max_entries: int = 128):
        self.max_entries = max_entries
        self._entries: OrderedDict[str, BaseModel] = OrderedDict()

    @staticmethod
    def make_key(*parts: str) -> str:
        raw_key = "\x00".join(parts)
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> BaseModel | None:
        result = self._entries.get(key)
        if result is not None:
            self._entries.move_to_end(key)
        return result

    def set(self, key: str, result: BaseModel) -> None:
        self._entries[key] = result
        self._entries.move_to_end(key)
        if len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
