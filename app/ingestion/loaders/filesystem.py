"""Loader de sistema de archivos: CÓMO llego al archivo.

Entrega bytes y no sabe qué hay dentro (eso es trabajo del parser). Mañana
puede haber un loader de Drive o de S3 con la misma forma.

Resuelve `CatalogSource.location` contra la raíz de datos (INGESTION_DATA_ROOT)
y devuelve un LoadedBlob por archivo con la extensión declarada en el
catálogo. Una ubicación que no existe es un error fuerte: un catálogo que
apunta a la nada está mal en silencio, la peor forma de estar roto.

Portado de app/ingestion/loaders/filesystem.py del curso.
"""

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LoadedBlob:
    relative_path: str  # ruta relativa a la raíz de datos
    bytes_: bytes


class FileSystemLoader:
    def __init__(self, data_root: Path | str) -> None:
        self.data_root = Path(data_root)

    def iter_blobs(self, location: str, formats: set[str]) -> Iterator[LoadedBlob]:
        path = self.data_root / location
        if not path.exists():
            raise FileNotFoundError(
                f"La ubicación del catálogo {location!r} no existe ({path})"
            )
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for candidate in candidates:
            if candidate.is_file() and candidate.suffix.lower().lstrip(".") in formats:
                yield LoadedBlob(
                    relative_path=candidate.relative_to(self.data_root).as_posix(),
                    bytes_=candidate.read_bytes(),
                )
