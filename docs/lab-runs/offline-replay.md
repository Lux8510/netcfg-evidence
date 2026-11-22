# 离线回放验收 · 2026-11-22T06:07:51+00:00

- 模式：`offline-replay`
- netcfg：`cab9b54b6e7a25dd2bfeba8e9c85280ad9fb2978`
- netsecops：`649a54354e79ac5b72493dc1c956f71c07e1ac44`
- 环境/拓扑：`lab/ospf`

| 场景 | 结果 |
| --- | --- |
| normal_replay | status=no_draft, submitted_by=lab-admin, config_version_id=0200b235-a021-46e9-b091-ab042175e963, rule_run_id=afefc373-da40-4f47-bc19-b65a1b18f614, findings=3 |
| area_mismatch_replay | status=draft, finding_rule=R005, change_status=approved, approved_by=lab-approver, duplicate=True |
| wrong_context_rejected | rights_unchanged=True, wrong_device_unchanged=True |

> 这是保存轨迹的离线回放，不是现场 FRR 采集；现场采集需要 Linux/WSL2 + Docker + Containerlab。
