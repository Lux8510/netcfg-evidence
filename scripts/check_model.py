"""One real model call, no database writes. Prints the explanation, citations and token usage.

Never prints the API key. Exits non-zero if the provider is not configured or the
model output is rejected by the validator.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from domain.netcfg.explain import validate_explanation  # noqa: E402
from domain.netcfg.providers import build_explainer, settings_from_env  # noqa: E402
from domain.netcfg.rules import RULES, Evidence, Finding  # noqa: E402


def main() -> int:
    settings = settings_from_env()
    print(f"provider={settings.provider} model={settings.model} base_url={settings.base_url} "
          f"key={'set' if settings.api_key else 'missing'}")
    if settings.provider == "mock":
        print("EXPLAIN_PROVIDER 仍是 mock；请设置真实 provider 后再验证")
        return 2

    rule = next(item for item in RULES if item.id == "R001")
    finding = Finding(
        rule_id="R001", status="violated",
        message="接口 eth2 声明为 point-to-point，但未显式设置 ip ospf cost；将依赖默认值。",
        evidence=(Evidence("config_line", 14, "no ip ospf cost", "interface.eth2.ospf.cost"),),
    )
    explainer = build_explainer(settings)
    explanation = explainer.explain(finding, rule)
    validation = validate_explanation(explanation, finding, rule, [14])

    print("\n--- text ---")
    print(explanation.text)
    print("\n--- citations ---")
    for citation in explanation.citations:
        print(f"{citation.rule_id} lines={list(citation.lines)}")
    print("\n--- usage ---")
    print(explanation.usage or "（provider 未返回 usage）")
    print(f"\nvalidation: accepted={validation.accepted} reason={validation.reason}")
    return 0 if validation.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
