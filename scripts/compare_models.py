"""Compare explanation models on the same labelled findings.

    python scripts/compare_models.py --models mock,deepseek-chat,deepseek-reasoner

No key is required for `mock`. Real models read EXPLAIN_* from .env.
Raw outputs and per-case results are written under evals/model-runs/<timestamp>/.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from evals.model_cases import ModelCase, build_cases, rule_for  # noqa: E402
from domain.netcfg.explain import MockExplainer, validate_explanation  # noqa: E402
from domain.netcfg.providers import (  # noqa: E402
    OpenAICompatibleExplainer, ProviderError, ProviderSettings, settings_from_env,
)
from domain.netcfg.rules import Evidence, Finding  # noqa: E402

# Rough public list prices (CNY per 1M tokens). Update if the vendor changes them.
PRICES = {
    "deepseek-chat": {"input": 2.0, "output": 8.0},
    "deepseek-reasoner": {"input": 4.0, "output": 16.0},
    "mock": {"input": 0.0, "output": 0.0},
}


def make_explainer(model: str):
    if model == "mock":
        return MockExplainer()
    base = settings_from_env()
    if not base.api_key:
        raise SystemExit("缺少 API key：请在 .env 设置 EXPLAIN_API_KEY 或 DEEPSEEK_API_KEY")
    return OpenAICompatibleExplainer(ProviderSettings(
        provider=base.provider if base.provider != "mock" else "deepseek",
        base_url=base.base_url, model=model, api_key=base.api_key,
        timeout=max(base.timeout, 60), max_tokens=base.max_tokens))


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * len(ordered))) - 1))
    return ordered[index]


def run_model(model: str, cases: list[ModelCase], repeat: int) -> dict:
    explainer = make_explainer(model)
    rows = []
    for case in cases:
        rule = rule_for(case.rule_id)
        finding = Finding(rule_id=case.rule_id, status=case.status, message=case.message,
                          evidence=tuple(Evidence("config_line", item["line"], item["text"], item.get("path"))
                                         for item in case.evidence))
        for attempt in range(repeat):
            started = time.perf_counter()
            error = None
            text = ""
            citations = []
            usage = None
            status = "rejected"
            reason = "provider_error"
            try:
                explanation = explainer.explain(finding, rule)
                text, citations, usage = explanation.text, explanation.citations, explanation.usage
                validation = validate_explanation(explanation, finding, rule,
                                                  [item["line"] for item in case.evidence])
                status, reason = ("accepted" if validation.accepted else "rejected"), validation.reason
            except ProviderError as exc:
                error = exc.reason
                reason = exc.reason
            except Exception as exc:  # noqa: BLE001 - one case must not kill the run
                error = type(exc).__name__
                reason = "error"
            latency_ms = (time.perf_counter() - started) * 1000
            cited_lines = {line for citation in citations for line in citation.lines}
            cited_rules = {citation.rule_id for citation in citations}
            rows.append({
                "case_id": case.id, "attempt": attempt, "status": status, "reason": reason,
                "rule_id": case.rule_id, "expected_lines": sorted(case.expected_lines),
                "cited_lines": sorted(cited_lines), "cited_rules": sorted(cited_rules),
                "citation_exact": (status == "accepted" and cited_rules == {case.rule_id}
                                   and cited_lines == case.expected_lines),
                "latency_ms": round(latency_ms, 1),
                "prompt_tokens": (usage or {}).get("prompt_tokens"),
                "completion_tokens": (usage or {}).get("completion_tokens"),
                "model": model, "text": text, "error": error,
            })
    accepted = [row for row in rows if row["status"] == "accepted"]
    exact = [row for row in rows if row["citation_exact"]]
    prompt_tokens = sum(row["prompt_tokens"] or 0 for row in rows)
    completion_tokens = sum(row["completion_tokens"] or 0 for row in rows)
    price = PRICES.get(model, {"input": 0.0, "output": 0.0})
    cost = (prompt_tokens / 1_000_000) * price["input"] + (completion_tokens / 1_000_000) * price["output"]
    latencies = [row["latency_ms"] for row in rows]
    return {
        "model": model,
        "cases": len(rows),
        "accepted": len(accepted),
        "accepted_rate": round(len(accepted) / len(rows), 3) if rows else 0.0,
        "citation_exact": len(exact),
        "citation_exact_rate": round(len(exact) / len(rows), 3) if rows else 0.0,
        "json_invalid": sum(1 for row in rows if row["reason"] == "invalid_response"),
        "provider_errors": sum(1 for row in rows if row["reason"] == "provider_error"),
        "overclaim": sum(1 for row in rows if row["reason"] == "overclaim"),
        "latency_ms": {
            "p50": round(percentile(latencies, 0.50), 1),
            "p95": round(percentile(latencies, 0.95), 1),
            "mean": round(statistics.fmean(latencies), 1) if latencies else 0.0,
        },
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimated_cost_cny": round(cost, 4),
        "rows": rows,
    }


def markdown(results: list[dict]) -> str:
    lines = ["| 模型 | 结构接受 | 引用完全正确 | JSON 失败 | 服务错误 | 过度声明 | p50 | p95 | tokens(入/出) | 估算成本 |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for result in results:
        lines.append(
            f"| {result['model']} | {result['accepted']}/{result['cases']} "
            f"({result['accepted_rate']:.0%}) | {result['citation_exact']}/{result['cases']} "
            f"({result['citation_exact_rate']:.0%}) | {result['json_invalid']} | "
            f"{result['provider_errors']} | {result['overclaim']} | "
            f"{result['latency_ms']['p50']}ms | {result['latency_ms']['p95']}ms | "
            f"{result['prompt_tokens']}/{result['completion_tokens']} | "
            f"¥{result['estimated_cost_cny']:.4f} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default="mock,deepseek-chat,deepseek-reasoner")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--limit", type=int, default=15)
    args = parser.parse_args()

    models = [item.strip() for item in args.models.split(",") if item.strip()]
    cases = build_cases(args.limit)
    if not cases:
        raise SystemExit("没有可用的评测案例")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = ROOT / "evals/model-runs" / stamp
    out_dir.mkdir(parents=True)

    results = []
    for model in models:
        print(f"--- {model} ({len(cases)} cases) ---", flush=True)
        result = run_model(model, cases, max(1, args.repeat))
        results.append(result)
        (out_dir / f"{model.replace('/', '_')}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(markdown([result]), flush=True)

    report = {"schema_version": 1, "executed_at": stamp, "cases": len(cases),
              "repeat": args.repeat, "results": [{k: v for k, v in item.items() if k != "rows"}
                                                 for item in results]}
    (out_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                         encoding="utf-8")
    (out_dir / "report.md").write_text(markdown(results) + "\n", encoding="utf-8")
    print("\n" + markdown(results))
    print(f"\n原始输出：{out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
