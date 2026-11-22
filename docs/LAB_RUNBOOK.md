# 受控 FRR 拓扑试运行手册

本手册是**现场采集路径**的复跑说明。离线回放由 `tools/lab_replay_acceptance.py` 自动执行；现场路径需要 Linux/WSL2 + Docker + Containerlab + FRR 镜像。

## 本机阻塞

当前工作站为 macOS，Docker 不可用，未安装 Containerlab / FRR：

```
$ docker info
docker-unavailable
$ which containerlab frr vtysh
（无输出）
```

因此**现场 FRR 采集未执行**。已执行的是保存轨迹的离线回放（`docs/lab-runs/offline-replay.md`），两者不能混用。

## 路径说明（关键）

采集器输出的是 `observations.json`，不是 Agent 轨迹；`handoff.py` 需要轨迹。  
正确链路是：

```
collect_evidence.py  ->  observations.json
build_handoff_trace.py -> trace.json（把两端 running-config 观测写入 trace）
handoff.py --require-peer -> bundle.json
```

`build_handoff_trace.py` 不需要模型密钥。只有走 `agent_cli.py`（live 或 replay）生成模型轨迹时才需要 `DEEPSEEK_API_KEY`；本手册的主路径不需要。

## 变量与环境

```bash
export NETSECOPS="$HOME/Documents/Codex/netsecops-agent"
export NETCFG="$HOME/Documents/Codex/netcfg-evidence"
export NETSECOPS_PY="$NETSECOPS/.venv312/bin/python"
export NETCFG_PY="$NETCFG/.venv/bin/python"
export LAB_RUN="/tmp/netsecops-lab-$(date -u +%Y%m%dT%H%M%SZ)"
export LAB_ADMIN_PASSWORD="replace-with-lab-password"
mkdir -p "$LAB_RUN"
```

检查：

```bash
docker info >/dev/null && echo docker-ok
containerlab version
"$NETSECOPS_PY" --version
"$NETCFG_PY" --version
```

## 1. 起拓扑与正常采集

```bash
cd "$NETSECOPS"
sudo containerlab deploy -t labs/ospf-baseline/topology.clab.yml
bash labs/ospf-baseline/deploy-and-verify.sh

"$NETSECOPS_PY" labs/ospf-baseline/collect_evidence.py \
  --output "$LAB_RUN/normal"
```

期望 `"$LAB_RUN/normal/observations.json"` 存在，且 discovery_error 为空。

构建轨迹并导出正常包：

```bash
cd "$NETSECOPS/labs/ospf-baseline"
"$NETSECOPS_PY" build_handoff_trace.py \
  --observations "$LAB_RUN/normal/observations.json" \
  --target r1 --out "$LAB_RUN/normal-trace.json"
"$NETSECOPS_PY" handoff.py \
  --trace "$LAB_RUN/normal-trace.json" --device r1 --target r1 \
  --require-peer --out "$LAB_RUN/normal.json"
```

正常包应包含 r1 目标快照；peer 若采集齐全也会进入 `peer_snapshot`。

## 2. Area 不一致

修改实验设备 r2（只改容器，不碰生产）：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'interface eth1' -c 'ip ospf area 0.0.0.1' -c 'end'
sudo docker exec clab-netsecops-ospf-r2 vtysh -c 'show running-config'

"$NETSECOPS_PY" "$NETSECOPS/labs/ospf-baseline/collect_evidence.py" \
  --output "$LAB_RUN/area"
```

构建轨迹；`--require-peer` 会拒绝缺对端快照的 Area 包：

```bash
cd "$NETSECOPS/labs/ospf-baseline"
"$NETSECOPS_PY" build_handoff_trace.py \
  --observations "$LAB_RUN/area/observations.json" \
  --target r2 --out "$LAB_RUN/area-trace.json"
"$NETSECOPS_PY" handoff.py \
  --trace "$LAB_RUN/area-trace.json" --device r2 --target r2 \
  --require-peer --out "$LAB_RUN/area.json"
```

导入前预检两端快照：

```bash
"$NETSECOPS_PY" - <<PY
import json
bundle = json.load(open("$LAB_RUN/area.json", encoding="utf-8"))
assert bundle["config_snapshot"]["text"], "target snapshot missing"
assert bundle["peer_snapshot"] and bundle["peer_snapshot"]["text"], "peer snapshot missing"
print("snapshots ok:", bundle["config_snapshot"]["node"], bundle["peer_snapshot"]["node"])
PY
```

恢复实验配置：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'interface eth1' -c 'ip ospf area 0.0.0.0' -c 'end'
```

## 3. 临时 netcfg 环境

```bash
export NETCFG_DATABASE_URL="sqlite:///$LAB_RUN/netcfg.db"
export NETCFG_COOKIE_SECURE=false

cd "$NETCFG"
"$NETCFG_PY" scripts/create_user.py --username lab-admin --role admin
"$NETCFG_PY" scripts/create_user.py --username lab-approver --role approver
"$NETCFG_PY" -m uvicorn api.main:app --port 8099
```

另开一个终端，登记设备和映射（登录 cookie 复用）：

```bash
BASE="http://127.0.0.1:8099"
COOKIE="$LAB_RUN/cookie.txt"
curl -s -c "$COOKIE" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"username\":\"lab-admin\",\"password\":\"$LAB_ADMIN_PASSWORD\"}"

R1_ID=$(curl -s -b "$COOKIE" -X POST "$BASE/api/devices" \
  -H 'Content-Type: application/json' -d '{"name":"r1"}' \
  | "$NETCFG_PY" -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
R2_ID=$(curl -s -b "$COOKIE" -X POST "$BASE/api/devices" \
  -H 'Content-Type: application/json' -d '{"name":"r2"}' \
  | "$NETCFG_PY" -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')

curl -s -b "$COOKIE" -X POST "$BASE/api/mappings" -H 'Content-Type: application/json' \
  -d "{\"environment\":\"lab\",\"topology\":\"ospf\",\"node\":\"r1\",\"device_id\":\"$R1_ID\"}"
curl -s -b "$COOKIE" -X POST "$BASE/api/mappings" -H 'Content-Type: application/json' \
  -d "{\"environment\":\"lab\",\"topology\":\"ospf\",\"node\":\"r2\",\"device_id\":\"$R2_ID\"}"
curl -s -b "$COOKIE" -X POST "$BASE/api/links" -H 'Content-Type: application/json' \
  -d "{\"device_a_id\":\"$R1_ID\",\"iface_a\":\"eth1\",\"device_b_id\":\"$R2_ID\",\"iface_b\":\"eth1\"}"
```

上传：浏览器设备详情选择 `r1`/`r2`，填写环境 `lab`、拓扑 `ospf`，上传对应包。或用 CLI：

```bash
"$NETCFG_PY" scripts/import_diagnosis.py \
  --bundle "$LAB_RUN/normal.json" --base-url "$BASE" \
  --username lab-admin --device-id "$R1_ID" \
  --environment lab --topology ospf

"$NETCFG_PY" scripts/import_diagnosis.py \
  --bundle "$LAB_RUN/area.json" --base-url "$BASE" \
  --username lab-admin --device-id "$R2_ID" \
  --environment lab --topology ospf
```

Area 导入确认返回 `status=draft`；再在界面提交并由 `lab-approver` 批准。

## 4. 必须检查

- normal：`status=no_draft`；设备详情可回查提交人、原始观测、配置版本、规则结果。
- area：仅对应链路、绑定两端快照的 R005 生成 `draft`；提交/审批走既有流程。
- 同包重导：返回原导入 ID、`duplicate=true`，业务行不增长。
- 换错环境/目标设备：422、请求目标/映射目标/对端均零新增。
- 人工修改实验设备后重新采集，核对变更前后快照与规则结果。

## 5. 清理

```bash
cd "$NETSECOPS"
sudo containerlab destroy -t labs/ospf-baseline/topology.clab.yml --cleanup
rm -rf "$LAB_RUN"
```
