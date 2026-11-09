"""Request ids, structured counters and the metrics endpoint."""

from __future__ import annotations

from engine import observability


def test_request_id_is_generated_and_echoed(app_client):
    first = app_client.get("/api/health")
    assert first.headers["x-request-id"]
    second = app_client.get("/api/health", headers={"X-Request-ID": "trace-abc"})
    assert second.headers["x-request-id"] == "trace-abc"


def test_metrics_requires_login(anon_client):
    assert anon_client.get("/api/metrics").status_code == 401


def test_metrics_counts_requests_and_statuses(app_client):
    observability.reset()
    app_client.get("/api/health")
    app_client.get("/api/devices")
    app_client.get("/api/devices/not-a-device/configs")

    data = app_client.get("/api/metrics").json()
    counters = data["counters"]
    assert counters["requests_total"] >= 3
    assert counters["errors_4xx"] >= 1
    assert data["status"].get("200", 0) >= 2
    assert data["latency_ms"]["average"] >= 0
    assert any(entry[0] == "GET /api/health" for entry in data["top_paths"])


def test_metrics_records_model_usage():
    from domain.netcfg.providers import ProviderSettings, OpenAICompatibleExplainer
    from domain.netcfg.rules import RULES, Evidence, Finding
    import httpx, json

    observability.reset()

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({
            "text": "规则 R001 第 3 行显示缺少 cost。",
            "citations": [{"rule_id": "R001", "lines": [3]}],
        })}}], "usage": {"prompt_tokens": 11, "completion_tokens": 7}})

    rule = next(item for item in RULES if item.id == "R001")
    finding = Finding(rule_id="R001", status="violated", message="缺少 cost",
                      evidence=(Evidence("config_line", 3, "no ip ospf cost"),))
    explainer = OpenAICompatibleExplainer(
        ProviderSettings(provider="openai", base_url="https://model.test/v1", model="m",
                         api_key="k" * 8, timeout=2, max_tokens=50),
        httpx.Client(transport=httpx.MockTransport(handler), base_url="https://model.test"))
    explainer.explain(finding, rule)

    data = observability.snapshot()
    assert data["counters"]["model_calls_total"] == 1
    assert data["counters"]["model_prompt_tokens"] == 11
    assert data["counters"]["model_completion_tokens"] == 7
