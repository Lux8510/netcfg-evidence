"""Single source of truth for what is publishable / reviewable."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCLUDE_FILES = (
    "README.md", "DESIGN.md", "REVIEW_GUIDE.md", "AGENTS.md", "Makefile",
    "requirements-dev.txt", "pyproject.toml", "review-feedback/README.md",
)
INCLUDE_DIRS = ("api", "domain", "engine", "tests", "evals", "samples", "docs", "tools")
EXCLUDE_PARTS = {".venv", ".venv312", "__pycache__", ".pytest_cache", ".git"}
EXCLUDE_SUFFIXES = {".pyc", ".db", ".sqlite"}
EXCLUDE_NAMES = {".env", "report.json", "netcfg.db"}


def files() -> list[Path]:
    selected: list[Path] = []
    for name in INCLUDE_FILES:
        path = ROOT / name
        if path.is_file():
            selected.append(path)
    for directory in INCLUDE_DIRS:
        base = ROOT / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(ROOT)
            if any(part in EXCLUDE_PARTS for part in relative.parts):
                continue
            if path.suffix in EXCLUDE_SUFFIXES or path.name in EXCLUDE_NAMES:
                continue
            selected.append(path)
    return sorted(set(selected), key=lambda p: p.relative_to(ROOT).as_posix())
