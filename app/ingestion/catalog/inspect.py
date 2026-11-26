"""Inspección de una carpeta de datos: hechos, no opiniones.

Produce HECHOS (cantidad de archivos, tamaño, última modificación, formatos)
que sustentan las entradas del catálogo. A propósito NO puntúa la calidad ni
escribe el catálogo: las opiniones viven en el YAML, los hechos en el código.
El censo mide lo medible; la calidad la decide una persona.

    uv run python -m app.ingestion.catalog.inspect data/seed

Limitación: la "última modificación" es la fecha del sistema de archivos, y
git no la conserva (un clon nuevo muestra la fecha del checkout). Por eso el
catálogo deja escrito lo observado (`refresh_observed`): la fecha del archivo
es una pista, no una prueba.

Portado de app/ingestion/catalog/inspect.py del curso.
"""

import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class FolderFacts:
    folder: Path
    file_count: int = 0
    total_size_mb: float = 0.0
    latest_modified: datetime | None = None
    formats_detected: set[str] = field(default_factory=set)

    def add(self, path: Path) -> None:
        stat = path.stat()
        self.file_count += 1
        self.total_size_mb += stat.st_size / (1024 * 1024)
        modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        if self.latest_modified is None or modified > self.latest_modified:
            self.latest_modified = modified
        if path.suffix:
            self.formats_detected.add(path.suffix.lower().lstrip("."))

    def as_table_row(self) -> str:
        latest = (
            self.latest_modified.isoformat(timespec="seconds")
            if self.latest_modified
            else "-"
        )
        formats = ",".join(sorted(self.formats_detected)) or "-"
        return (
            f"{self.folder.name:<14} {self.file_count:>3} files  "
            f"{self.total_size_mb:>5.2f} MB  latest={latest:<26} formats={formats}"
        )


def _facts(folder: Path, files: Iterable[Path]) -> FolderFacts:
    facts = FolderFacts(folder=folder)
    for path in files:
        facts.add(path)
    return facts


def inspect_folder(folder: Path) -> FolderFacts:
    if not folder.exists():
        return FolderFacts(folder=folder)
    return _facts(folder, (p for p in folder.rglob("*") if p.is_file()))


def inspect_root(root: Path) -> list[FolderFacts]:
    """Cada subcarpeta de `root`, más los archivos sueltos en `root` (como
    el Excel de tarifas)."""
    if not root.exists():
        return []
    results = [
        inspect_folder(child) for child in sorted(root.iterdir()) if child.is_dir()
    ]
    loose = [p for p in root.iterdir() if p.is_file() and not p.name.startswith(".")]
    if loose:
        results.append(_facts(root, loose))
    return results


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("uso: python -m app.ingestion.catalog.inspect <carpeta>")
        return 1
    root = Path(argv[1])
    if not root.exists():
        print(f"ERROR: {root} no existe")
        return 2
    facts = inspect_root(root)
    if not facts:
        print(f"(no hay archivos en {root})")
        return 0
    print(f"Inspección de {root.resolve()}\n")
    for entry in facts:
        print(entry.as_table_row())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
