"""Fail if a reviewable file contains a credential, an absolute personal path, or a broken link."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_files import ROOT, files  # noqa: E402

SECRET_PATTERNS = {
    "openai-style key": re.compile(r"sk-[A-Za-z0-9]{10,}"),
    "github token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "bearer token": re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}"),
    "authorization header": re.compile(r"Authorization\s*[:=]", re.IGNORECASE),
    # Split literals so this checker cannot flag its own source.
    "absolute user path": re.compile(r"(?:" + "/Us" + "ers/|/ho" + r"me/[a-z]|[A-Za-z]:" + r"\\\\Us" + r"ers\\\\)"),
}
LINK = re.compile(r"\]\(([^)]+)\)")


def main() -> int:
    problems: list[str] = []
    checked = files()
    for path in checked:
        relative = path.relative_to(ROOT)
        if path.suffix not in {".md", ".py", ".toml", ".json", ".txt", ".yml", ".yaml", ".conf"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for name, pattern in SECRET_PATTERNS.items():
            if pattern.search(text):
                problems.append(f"{relative}: possible {name}")
        if path.suffix == ".md":
            for target in LINK.findall(text):
                if target.startswith(("http://", "https://", "#", "mailto:")):
                    continue
                relative_target = unquote(target.split("#", 1)[0])
                if not (path.parent / relative_target).exists():
                    problems.append(f"{relative}: broken link -> {target}")
    if problems:
        print("Publication check: FAIL")
        for problem in problems:
            print(" -", problem)
        return 1
    print(f"Publication check: PASS; files reviewed: {len(checked)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
