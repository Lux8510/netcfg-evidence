# 离线回放验收 · 2026-11-22T07:59:44+00:00

- 模式：`offline-replay`
- netcfg：`3b2e6e28421d4fa67aed56c1b28f4c3a3c961481`
- netsecops：`dfdda109152ab74fa1bf16e2bcc409a30dea6983`
- 环境/拓扑：`lab/ospf`

| 场景 | 结果 |
| --- | --- |
| normal_replay | status=no_draft, submitted_by=lab-admin, config_version_id=10c51d06-2f41-4ed6-91f0-61416398359b, rule_run_id=b840c5c5-4672-40b8-a2c4-64cc66066bbb, findings=3 |
| area_mismatch_replay | status=draft, finding_rule=R005, change_status=approved, approved_by=lab-approver, duplicate=True |
| wrong_context_rejected | rights_unchanged=True, wrong_device_unchanged=True |

> 这是保存轨迹的离线回放，不是现场 FRR 采集；现场采集需要 Linux/WSL2 + Docker + Containerlab。
