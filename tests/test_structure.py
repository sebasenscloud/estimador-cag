from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

REQUIRED_FILES = [
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/routers/__init__.py",
    "app/routers/estimations.py",
    "app/services/__init__.py",
    "app/services/llm_service.py",
    "app/context/__init__.py",
    "app/context/examples.py",
    ".env.example",
    ".gitignore",
    "pyproject.toml",
]


@pytest.mark.parametrize("relative_path", REQUIRED_FILES)
def test_required_file_exists(relative_path):
    assert (PROJECT_ROOT / relative_path).is_file(), f"Falta el archivo {relative_path}"


def test_env_file_is_ignored_by_git():
    gitignore_lines = (PROJECT_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gitignore_lines, (
        "El .env debe estar en .gitignore para no subir la API key"
    )
