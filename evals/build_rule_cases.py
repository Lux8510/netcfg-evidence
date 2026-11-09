"""Build the rule-judgement evaluation set. Expectations are declared by hand."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = (ROOT / "samples/frr/r1.conf").read_text(encoding="utf-8")
OUT = ROOT / "evals/rule_cases.json"

SAT = {"R001": "satisfied", "R002": "satisfied", "R003": "satisfied", "R004": "satisfied"}


def expect(**overrides: str) -> dict[str, str]:
    merged = dict(SAT)
    merged.update(overrides)
    return merged


CASES: list[dict] = []


def case(case_id: str, description: str, current: str, expected: dict[str, str]) -> None:
    CASES.append({"id": case_id, "description": description, "current": current, "expected": expected})


def rep(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


case("R01", "基线配置全部符合", BASELINE, expect())
case("R02", "移除 eth2 的 cost", rep(BASELINE, " ip ospf cost 10\n", ""), expect(R001="violated"))
case("R03", "先设 cost 再撤销", rep(BASELINE, " ip ospf cost 10\n", " ip ospf cost 10\n no ip ospf cost\n"),
     expect(R001="violated"))
case("R04", "出现未识别的 OSPF 否定命令",
     rep(BASELINE, " ip ospf cost 10\n", " ip ospf cost 10\n no ip ospf authentication\n"),
     expect(R001="undetermined", R002="undetermined", R003="undetermined", R004="undetermined"))
case("R05", "宣告不存在的网段",
     rep(BASELINE, " network 10.13.0.0/30 area 0.0.0.0", " network 10.77.0.0/30 area 0.0.0.0"),
     expect(R002="violated"))
case("R06", "Loopback 未设 passive", rep(BASELINE, " passive-interface lo\n", ""), expect(R003="violated"))
case("R07", "router-id 不是 Loopback", rep(BASELINE, " ospf router-id 10.255.1.1", " ospf router-id 1.1.1.1"),
     expect(R004="violated"))
case("R08", "同时违反 R002 与 R003",
     rep(rep(BASELINE, " network 10.13.0.0/30 area 0.0.0.0", " network 10.77.0.0/30 area 0.0.0.0"),
         " passive-interface lo\n", ""),
     expect(R002="violated", R003="violated"))
case("R09", "完全没有 OSPF 配置",
     "hostname r9\ninterface eth1\n ip address 10.0.0.1/30\n!\n",
     {"R001": "not_applicable", "R002": "not_applicable", "R003": "not_applicable", "R004": "not_applicable"})
case("R10", "声明了 OSPF 但进程块无法解析",
     "hostname r10\nrouter ospf 1\n!\n",
     expect(R001="undetermined", R002="undetermined", R003="undetermined", R004="undetermined"))
case("R11", "撤销 cost 且移除 Loopback passive",
     rep(rep(BASELINE, " ip ospf cost 10\n", " ip ospf cost 10\n no ip ospf cost\n"),
         " passive-interface lo\n", ""),
     expect(R001="violated", R003="violated"))
case("R12", "新增 default-information 不影响任何规则",
     rep(BASELINE, " passive-interface eth1\n", " passive-interface eth1\n default-information originate\n"),
     expect())
case("R13", "接口 shutdown 不影响当前四条规则",
     rep(BASELINE, " ip address 10.12.0.1/30", " ip address 10.12.0.1/30\n shutdown"), expect())
case("R14", "地址变更不影响当前四条规则",
     rep(BASELINE, " ip address 10.12.0.1/30", " ip address 10.12.0.2/30"), expect())
case("R15", "三处同时违规",
     rep(rep(rep(BASELINE, " ip ospf cost 10\n", ""), " passive-interface lo\n", ""),
         " ospf router-id 10.255.1.1", " ospf router-id 2.2.2.2"),
     expect(R001="violated", R003="violated", R004="violated"))


def main() -> None:
    payload = {"schema_version": 1, "baseline": BASELINE, "cases": CASES}
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(CASES)} rule cases -> {OUT}")


if __name__ == "__main__":
    main()
