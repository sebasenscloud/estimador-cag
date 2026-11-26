"""Pre-flight de la Sesión 6: comprobar que el stack está sano ANTES de construir.

Basado en scripts/preflight_s06.py del curso. Cada comprobación es una función:
si falla, imprime el motivo en lugar de dejar que lo descubras a mitad de una
demo. Sale con código 1 si alguna falla.

    uv run python -m scripts.preflight_s06

La más importante es la de spaCy: Presidio usa spaCy para detectar personas,
y sin el modelo en español (es_core_news_md) devuelve CERO nombres hispanos sin
avisar. La pseudonimización del paso 2.5 parecería funcionar y los nombres
saldrían en claro.

Diferencias con el curso:
- Sin `unstructured` (cientos de MB): los PDFs ya los resuelven pypdf/PyMuPDF.
- Sin Postgres: la tabla de mapeo de seudónimos vive en memoria por ahora.
- Las comprobaciones de piezas que todavía no existen (corpus, catálogo,
  almacén de mapeos) salen como ⏳ pendientes con el paso que las crea, no
  como fallas: el pre-flight crece con la sesión.
"""

import importlib.metadata as importlib_metadata
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

OK, FAIL, PENDING = "✅", "❌", "⏳"
ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "seed"
CATALOG = ROOT / "data" / "catalog" / "catalog.yaml"


class Pending(Exception):
    """La pieza todavía no existe: la crea un paso posterior de la sesión."""


class Check:
    def __init__(self, name: str, fn: Callable[[], str | None]) -> None:
        self.name = name
        self.fn = fn

    def run(self) -> str:
        """Imprime el resultado y devuelve "ok", "pending" o "fail"."""
        try:
            detail = self.fn()
        except Pending as pending:
            print(f"{PENDING}  {self.name}: {pending}")
            return "pending"
        except Exception as exc:  # noqa: BLE001
            print(f"{FAIL}  {self.name}: {type(exc).__name__}: {exc}")
            return "fail"
        print(f"{OK}  {self.name}" + (f": {detail}" if detail else ""))
        return "ok"


def check_python_version() -> str:
    # Se ejecuta aunque el intérprete sea otro (por eso el chequeo en runtime).
    if sys.version_info < (3, 11):  # noqa: UP036
        raise RuntimeError(f"hace falta Python 3.11+, hay {sys.version.split()[0]}")
    return sys.version.split()[0]


MIN_VERSIONS = {
    "pandas": "2.2",
    "pandera": "0.20",
    "pyyaml": "6.0",
    "openpyxl": "3.1",
    "presidio-analyzer": "2.2",
    "presidio-anonymizer": "2.2",
    "spacy": "3.7",
    "faker": "24.0",
    "es-core-news-md": "3.7",
}


def _version(text: str) -> tuple[int, ...]:
    return tuple(
        int("".join(c for c in part if c.isdigit()) or 0) for part in text.split(".")
    )


def check_packages() -> str:
    problems = []
    for package, minimum in MIN_VERSIONS.items():
        try:
            installed = importlib_metadata.version(package)
        except importlib_metadata.PackageNotFoundError:
            problems.append(f"falta {package}")
            continue
        if _version(installed) < _version(minimum):
            problems.append(f"{package} {installed} < {minimum}")
    if problems:
        raise RuntimeError("; ".join(problems) + " → corré `uv sync`")
    return f"{len(MIN_VERSIONS)} paquetes OK"


def check_spacy_model() -> str:
    import spacy

    nlp = spacy.load("es_core_news_md")
    persons = [
        e.text
        for e in nlp("Laura Fernández firmó en Madrid.").ents
        if e.label_ == "PER"
    ]
    if "Laura Fernández" not in persons:
        raise RuntimeError(
            "es_core_news_md cargó pero NO detectó 'Laura Fernández' como persona"
        )
    return f"detecta personas: {persons}"


def check_corpus_seed() -> str:
    if not SEED.exists():
        raise Pending("data/seed/ todavía no existe (paso 2.2)")
    budgets = list((SEED / "budgets").glob("*.json"))
    transcripts = list((SEED / "transcripts").glob("*.txt"))
    sheets = list(SEED.glob("*.xlsx"))
    if not budgets or not transcripts or not sheets:
        raise RuntimeError(
            f"corpus incompleto: {len(budgets)} json, {len(transcripts)} txt, "
            f"{len(sheets)} xlsx"
        )
    # Error típico de formato: un archivo de texto en Latin-1 rompe el parser.
    not_utf8 = []
    for path in budgets + transcripts:
        try:
            path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            not_utf8.append(path.name)
    if not_utf8:
        raise RuntimeError(f"no son UTF-8: {not_utf8}")
    return f"{len(budgets)} json + {len(transcripts)} txt + {len(sheets)} xlsx, UTF-8"


def check_catalog() -> str:
    if not CATALOG.exists():
        raise Pending("data/catalog/catalog.yaml todavía no existe (paso 2.2)")
    from app.ingestion.catalog import load_catalog

    catalog = load_catalog(CATALOG)
    return f"versión {catalog.version}, {len(catalog.included_sources())} incluida(s)"


def check_health_endpoint() -> str:
    from app.config import settings

    # El health check del servicio es GET / (app/main.py).
    url = f"{settings.estimator_api_url}/"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return f"{url} → {response.status}"
    except Exception as exc:
        raise RuntimeError(
            f"{url} no responde: ¿está corriendo el servicio? ({exc})"
        ) from exc


def check_mapping_store() -> str:
    try:
        from app.ingestion.pii import InMemoryMappingStore
    except ImportError as exc:
        raise Pending("el almacén de seudónimos llega en el paso 2.5") from exc
    store = InMemoryMappingStore()
    first = store.lookup_or_create("PERSON", "hash-de-prueba", lambda: "Ana Pérez")
    again = store.lookup_or_create("PERSON", "hash-de-prueba", lambda: "Otro")
    if first != again:
        raise RuntimeError("el mismo valor no devolvió el mismo seudónimo")
    return "en memoria: mismo valor → mismo seudónimo"


CHECKS = [
    Check("Versión de Python", check_python_version),
    Check("Paquetes requeridos", check_packages),
    Check("spaCy es_core_news_md", check_spacy_model),
    Check("Corpus de ejemplo", check_corpus_seed),
    Check("Catálogo válido", check_catalog),
    Check("Estimador (health)", check_health_endpoint),
    Check("Almacén de seudónimos", check_mapping_store),
]


def main() -> int:
    print("Pre-flight Sesión 6\n")
    results = [check.run() for check in CHECKS]
    failed, pending = results.count("fail"), results.count("pending")
    print()
    if failed:
        print(f"{FAIL}  {failed} comprobación(es) fallaron: arreglalas antes de seguir")
        return 1
    suffix = f" ({pending} pendiente(s) de pasos posteriores)" if pending else ""
    print(f"{OK}  Todo en orden{suffix}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
