from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent

REQUIRED_FILES = [
    "app/__init__.py",
    "app/main.py",
    "app/config.py",
    "app/schemas.py",
    "app/routers/__init__.py",
    "app/routers/estimations.py",
    "app/services/__init__.py",
    "app/services/llm_service.py",
    "app/services/cache.py",
    "app/services/providers/__init__.py",
    "app/services/providers/base.py",
    "app/services/providers/anthropic_provider.py",
    "app/services/providers/openai_provider.py",
    "app/logging_config.py",
    "streamlit_app.py",
    "app/prompts/loader.py",
    "app/prompts/estimation/v1/system.j2",
    "app/prompts/estimation/v1/user.j2",
    "app/prompts/estimation/v1/examples.j2",
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
