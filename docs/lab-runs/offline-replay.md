# 离线回放验收 · 2026-11-22T06:31:57+00:00

- 模式：`offline-replay`
- netcfg：`c92fcbe0c290041806f46ad4f498446c40ed948d`
- netsecops：`c86c8bdcbe2a71934adf0d6ef325715a360a5687`
- 环境/拓扑：`lab/ospf`

| 场景 | 结果 |
| --- | --- |
| normal_replay | status=no_draft, submitted_by=lab-admin, config_version_id=944c5383-8963-4f84-8a7d-fd930a98f9a4, rule_run_id=0de622aa-1595-43c1-8962-1617aa1a46a7, findings=3 |
| area_mismatch_replay | status=draft, finding_rule=R005, change_status=approved, approved_by=lab-approver, duplicate=True |
| wrong_context_rejected | rights_unchanged=True, wrong_device_unchanged=True |

> 这是保存轨迹的离线回放，不是现场 FRR 采集；现场采集需要 Linux/WSL2 + Docker + Containerlab。
