"""Real-model adapter for the explanation layer.

The model never decides anything: it receives one reviewed finding and must return
JSON with the same rule id and evidence line numbers. Anything else is rejected by
the validator in `explain.py`, exactly like the Mock output.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

import httpx

from .explain import Citation, Explanation, Explainer, ProviderError
from .rules import Finding as RuleFinding, Rule

SYSTEM_PROMPT = (
    "你是网络配置审查的解释器。你只能解释已经给出的判定，不能改变判定，也不能提出新的问题。\n"
    "要求：\n"
    "1. 只使用给定的规则 ID 和证据行号，不得编造其他行号或规则。\n"
    "2. 正文必须明确写出被引用的行号，例如「第 14 行」。\n"
    "3. 不得声称已经修改、下发或修复任何配置。\n"
    "4. 只输出 JSON：{\"text\": string, \"citations\": [{\"rule_id\": string, \"lines\": [int]}]}\n"
)


@dataclass(frozen=True)
class ProviderSettings:
    provider: str = "mock"
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    api_key: str = ""
    timeout: float = 20.0
    max_tokens: int = 700


def settings_from_env(env: dict[str, str] | None = None) -> ProviderSettings:
    env = env if env is not None else os.environ
    return ProviderSettings(
        provider=(env.get("EXPLAIN_PROVIDER") or "mock").strip().lower(),
        base_url=(env.get("EXPLAIN_BASE_URL") or "https://api.deepseek.com/v1").strip(),
        model=(env.get("EXPLAIN_MODEL") or "deepseek-chat").strip(),
        api_key=(env.get("EXPLAIN_API_KEY") or env.get("DEEPSEEK_API_KEY") or env.get("OPENAI_API_KEY") or "").strip(),
        timeout=float(env.get("EXPLAIN_TIMEOUT") or 20),
        max_tokens=int(env.get("EXPLAIN_MAX_TOKENS") or 700),
    )


def build_explainer(settings: ProviderSettings | None = None) -> Explainer:
    from .explain import MockExplainer

    settings = settings or settings_from_env()
    if settings.provider == "mock":
        return MockExplainer()
    if settings.provider in {"openai", "deepseek"}:
        if not settings.api_key:
            from .service import ServiceError
            raise ServiceError("MODEL_NOT_CONFIGURED",
                               "已选择真实模型，但未配置 EXPLAIN_API_KEY / DEEPSEEK_API_KEY；不会退回 Mock 假装成功", 409)
        return OpenAICompatibleExplainer(settings)
    raise ProviderError("invalid_configuration", f"未知的 EXPLAIN_PROVIDER：{settings.provider}")


def _user_prompt(finding: RuleFinding, rule: Rule) -> str:
    evidence = "\n".join(
        f"- 第 {item.line} 行：{item.text}" for item in finding.evidence if item.line
    ) or "（无行号证据）"
    return (
        f"规则：{rule.id} — {rule.title}\n"
        f"规则依据：{rule.rationale}\n"
        f"判定：{finding.status}\n"
        f"判定说明：{finding.message}\n"
        f"证据：\n{evidence}\n"
        f"请只引用上面的行号解释这条判定。"
    )


class OpenAICompatibleExplainer:
    def __init__(self, settings: ProviderSettings, client: httpx.Client | None = None) -> None:
        self.settings = settings
        self._client = client
        self.name = settings.model

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.settings.timeout)
        return self._client

    def explain(self, finding: RuleFinding, rule: Rule) -> Explanation:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _user_prompt(finding, rule)},
            ],
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
            "response_format": {"type": "json_object"},
        }
        try:
            response = self.client.post(
                self.settings.base_url.rstrip("/") + "/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            raise ProviderError("provider_error", f"模型请求失败：{type(exc).__name__}") from exc

        if response.status_code != 200:
            raise ProviderError("provider_error", f"模型返回 HTTP {response.status_code}，未采用其输出")

        payload = response.json()
        try:
            content = payload["choices"][0]["message"]["content"]
            data = json.loads(content)
            text = data["text"]
            citations = tuple(
                Citation(str(item["rule_id"]), tuple(int(line) for line in item["lines"]))
                for item in data["citations"]
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("invalid_response", "模型输出不是约定的 JSON 结构，已丢弃") from exc

        if not isinstance(text, str) or not isinstance(citations, tuple):
            raise ProviderError("invalid_response", "模型输出字段类型不符合约定，已丢弃")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        return Explanation(text=text, citations=citations, model=self.settings.model,
                           usage=usage if isinstance(usage, dict) else None)
