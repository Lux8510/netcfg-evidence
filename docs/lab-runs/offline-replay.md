# 离线回放验收 · 2026-11-22T06:07:39+00:00

- 模式：`offline-replay`
- netcfg：`8ce5ac15be4af9b8ec3a680332f289f423162988`
- netsecops：`649a54354e79ac5b72493dc1c956f71c07e1ac44`
- 环境/拓扑：`lab/ospf`

| 场景 | 结果 |
| --- | --- |
| normal_replay | status=no_draft, submitted_by=lab-admin, config_version_id=82f51ce6-4a31-4c97-b270-05c81cf13928, rule_run_id=563a5ab3-e0c7-491b-ad49-383f1c1aa7e5, findings=3 |
| area_mismatch_replay | status=draft, finding_rule=R005, change_status=approved, approved_by=lab-approver, duplicate=True |
| wrong_context_rejected | rights_unchanged=True, wrong_device_unchanged=True |

> 这是保存轨迹的离线回放，不是现场 FRR 采集；现场采集需要 Linux/WSL2 + Docker + Containerlab。
