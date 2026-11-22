# 受控 FRR 拓扑试运行手册

本手册是现场采集路径的复跑说明。离线回放由 `tools/lab_replay_acceptance.py` 自动执行；现场路径需要 Linux/WSL2 + Docker + Containerlab + FRR 镜像。

## 本机阻塞

当前工作站为 macOS，`docker info` 失败，未安装 `containerlab` / FRR：

```
docker-unavailable
```

因此**现场 FRR 采集未执行**；已完成保存轨迹的离线回放（见 `docs/lab-runs/offline-replay.md`）。不要将离线回放结果当作现场采集结果。

## 前置条件

- Linux 或 WSL2，Docker 可用，`containerlab` 已安装
- NetSecOps venv 已安装 `requirements-progress.txt`
- netcfg venv 已安装 `requirements-dev.txt`
- 允许 `sudo -n docker`（实验驱动使用）
- 可选的 DeepSeek key（若用真实模型生成 trace；没有 key 时用离线诊断/保存轨迹）

检查：

```bash
docker info >/dev/null && echo docker-ok
containerlab version
python3 --version
```

## 1. 起实验拓扑

```bash
cd netsecops-agent
sudo containerlab deploy -t labs/ospf-baseline/topology.clab.yml
# 或按仓库现有脚本做基线验证
bash labs/ospf-baseline/deploy-and-verify.sh
```

正常路径采集：

```bash
python labs/ospf-baseline/collect_evidence.py \
  --output /tmp/netsecops-live-normal
```

生成可交接 trace（真实模型路径，示例）：

```bash
cd labs/ospf-baseline
DEEPSEEK_API_KEY=... python agent_cli.py --mode live --run-label live-normal
# 输出末尾会打印 Trace: .../trace.json
python handoff.py --trace <trace.json> --device r1 --target r1 --out /tmp/normal-handoff.json
```

## 2. 复现 Area 不一致

在实验设备 r2 的互联接口上改 Area（只改实验容器，不碰生产）：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'interface eth1' -c 'ip ospf area 0.0.0.1' -c 'end'
sudo docker exec clab-netsecops-ospf-r2 vtysh -c 'show running-config'
```

采集并生成 r2 交接包：

```bash
cd netsecops-agent
python labs/ospf-baseline/collect_evidence.py --output /tmp/netsecops-live-area
cd labs/ospf-baseline
DEEPSEEK_API_KEY=... python agent_cli.py --mode live --run-label live-fault
python handoff.py --trace <trace.json> --device r2 --target r2 --out /tmp/area-handoff.json
```

恢复实验配置：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'interface eth1' -c 'ip ospf area 0.0.0.0' -c 'end'
```

## 3. 临时 netcfg 环境

```bash
cd netcfg-evidence
export NETCFG_DATABASE_URL=sqlite:////tmp/lab-netcfg.db
.venv/bin/python scripts/create_user.py --username lab-admin --role admin
.venv/bin/python scripts/create_user.py --username lab-approver --role approver
.venv/bin/python -m uvicorn api.main:app --port 8099
```

登记环境/拓扑/映射/链路（以实际 device id 替换）：

```bash
BASE=http://127.0.0.1:8099
curl -s -c /tmp/lab.jar -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' -d '{"username":"lab-admin","password":"..."}'
curl -s -b /tmp/lab.jar -X POST $BASE/api/devices -H 'Content-Type: application/json' -d '{"name":"r1"}'
curl -s -b /tmp/lab.jar -X POST $BASE/api/devices -H 'Content-Type: application/json' -d '{"name":"r2"}'
curl -s -b /tmp/lab.jar -X POST $BASE/api/mappings -H 'Content-Type: application/json' \
  -d '{"environment":"lab","topology":"ospf","node":"r1","device_id":"<r1-id>"}'
curl -s -b /tmp/lab.jar -X POST $BASE/api/mappings -H 'Content-Type: application/json' \
  -d '{"environment":"lab","topology":"ospf","node":"r2","device_id":"<r2-id>"}'
curl -s -b /tmp/lab.jar -X POST $BASE/api/links -H 'Content-Type: application/json' \
  -d '{"device_a_id":"<r1-id>","iface_a":"eth1","device_b_id":"<r2-id>","iface_b":"eth1"}'
```

从设备详情上传 `/tmp/normal-handoff.json`（环境 `lab`、拓扑 `ospf`），确认“仅存证”；再上传 `/tmp/area-handoff.json`，确认“已生成草稿”并走人工提交/审批。也可用 CLI：

```bash
.venv/bin/python scripts/import_diagnosis.py --bundle /tmp/normal-handoff.json \
  --base-url $BASE --username lab-admin --device-id <r1-id> --environment lab --topology ospf
.venv/bin/python scripts/import_diagnosis.py --bundle /tmp/area-handoff.json \
  --base-url $BASE --username lab-admin --device-id <r2-id> --environment lab --topology ospf
```

## 4. 必须检查

- normal：`status=no_draft`，记录含提交人、原始观测、配置版本、规则运行；
- area：唯一的 R005 Finding 绑定导入版本与两端快照，草稿状态 `draft`，提交/审批走既有流程；
- 同包重导：返回原导入 ID、`duplicate=true`，业务行不增长；
- 换错环境或目标设备：422、零新增；
- 人工修改实验设备后重新采集，核对前后快照与规则结果。

## 5. 清理

```bash
sudo containerlab destroy -t labs/ospf-baseline/topology.clab.yml --cleanup
rm -f /tmp/lab-netcfg.db /tmp/normal-handoff.json /tmp/area-handoff.json
```
