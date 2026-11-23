# 受控 FRR 拓扑试运行手册

本手册是**现场采集路径**的复跑说明。离线回放由 `tools/lab_replay_acceptance.py` 自动执行；现场路径需要 Linux/WSL2 + Docker + Containerlab + FRR 镜像。

## 实测环境

Colima Linux VM（arm64）、Containerlab 0.83.0、FRR 10.7.1 已采集正常、Area 不一致、恢复后三组真实观测。此前的保存轨迹回放（`docs/lab-runs/offline-replay.md`）是独立检查。Docker、Containerlab 和采集必须在同一 Linux VM 内运行；Colima 下用 `colima ssh -p netcfg-lab` 进入 VM，仓库和归档选用宿主机可读写的共享目录。

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
export LAB_RUN="$NETSECOPS/../netsecops-lab-$(date -u +%Y%m%dT%H%M%SZ)"
# 在宿主机与 VM 使用同一个 LAB_RUN 值，不要分别生成时间戳。
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
```

采集 CLI `collect_evidence.py --output` 有 WSL2 内核检查，普通 Linux/Colima 会拒绝。在运行容器的 Linux shell 中使用系统 `python3` 调同一个采集函数（需 `sudo -n docker` 可用）；不要使用宿主机的 macOS venv：

```bash
cd "$NETSECOPS/labs/ospf-baseline"
collect_lab() {
  python3 - "$1" <<'PY'
import sys
from pathlib import Path
from collect_evidence import LAB_DIR, collect_evidence
result = collect_evidence(LAB_DIR, Path(sys.argv[1]).resolve())
if result['discovery_error']:
    sys.exit(result['discovery_error'])
PY
}

check_converged() {
  sudo -n docker exec clab-netsecops-ospf-r1 vtysh -c 'show ip ospf neighbor' &&
  sudo -n docker exec clab-netsecops-ospf-r2 vtysh -c 'show ip ospf neighbor' &&
  sudo -n docker exec clab-netsecops-ospf-r1 vtysh -c 'show ip route ospf' &&
  sudo -n docker exec clab-netsecops-ospf-r2 vtysh -c 'show ip route ospf' &&
  sudo -n docker exec clab-netsecops-ospf-r1 ping -c 2 -W 1 10.255.0.2 &&
  sudo -n docker exec clab-netsecops-ospf-r2 ping -c 2 -W 1 10.255.0.1
}
check_converged
```

确认两端邻居均为 `Full`，各有对端 Loopback `/32` 路由，双向 ping 成功，再运行 `collect_lab "$LAB_RUN/normal"`。启动过早可能只有邻居、没有路由；此时等待并重查，不把 `insufficient_evidence` 当正常。Colima 宿主机上的构建/导出使用独立 NetSecOps venv，指向同一共享归档目录。

```bash
# 在 Linux 采集 shell 中，收敛确认后运行
collect_lab "$LAB_RUN/normal"
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
  --require-peer --expect-status normal --out "$LAB_RUN/normal.json"
```

正常包应包含 r1 目标快照；`--require-peer` 还会要求 r2 对端快照，缺任一端直接失败。

## 2. Area 不一致

修改实验设备 r2（只改容器，不碰生产）：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'router ospf' \
  -c 'no network 10.0.12.0/30 area 0.0.0.0' \
  -c 'network 10.0.12.0/30 area 0.0.0.1' -c 'end' &&
collect_lab "$LAB_RUN/area"
```

构建轨迹；`--require-peer` 会拒绝缺对端快照，`--expect-status ospf_area_mismatch` 会拒绝诊断降级（例如缺对端后变成 insufficient_evidence）：

```bash
cd "$NETSECOPS/labs/ospf-baseline"
"$NETSECOPS_PY" build_handoff_trace.py \
  --observations "$LAB_RUN/area/observations.json" \
  --target r2 --out "$LAB_RUN/area-trace.json"
"$NETSECOPS_PY" handoff.py \
  --trace "$LAB_RUN/area-trace.json" --device r2 --target r2 \
  --require-peer --expect-status ospf_area_mismatch \
  --out "$LAB_RUN/area.json"
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

保持实验故障，继续第 3 节完成导入和审批，再按第 4 节人工恢复。此拓扑的 Area 来自 `router ospf` 的 `network` 声明；接口级 `ip ospf area` 曾被 FRR 拒绝。

## 3. 临时 netcfg 环境

```bash
export NETCFG_DATABASE_URL="sqlite:///$LAB_RUN/netcfg.db"
export NETCFG_COOKIE_SECURE=false
export EXPLAIN_PROVIDER=mock
read -rsp '实验管理员密码: ' LAB_ADMIN_PASSWORD; echo

cd "$NETCFG"
NETCFG_USER_PASSWORD="$LAB_ADMIN_PASSWORD" "$NETCFG_PY" scripts/create_user.py --username lab-admin --role admin
"$NETCFG_PY" scripts/create_user.py --username lab-approver --role approver
"$NETCFG_PY" -m uvicorn api.main:app --port 8099
```

另开一个终端，登记设备和映射（登录 cookie 复用）：

```bash
cd "$NETCFG"
read -rsp '实验管理员密码: ' LAB_ADMIN_PASSWORD; echo
BASE="http://127.0.0.1:8099"
COOKIE="$LAB_RUN/cookie.txt"
curl -s -c "$COOKIE" -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<< "$(LAB_ADMIN_PASSWORD="$LAB_ADMIN_PASSWORD" "$NETCFG_PY" -c 'import json,os; print(json.dumps(dict(username="lab-admin", password=os.environ["LAB_ADMIN_PASSWORD"])))')"

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

## 4. 审批后人工恢复及核对

先提交 R005 草稿，由独立 `lab-approver` 批准并回查 `approved`、审批人和建议。保存 Area 包、两端快照、导入记录及对应 `rule_run_id` 的 Findings（R005 应为 `violated`）。确认容器当前配置仍与获批前快照相同；不匹配则停止，重新采集和审批。**netcfg 不执行建议**，批准后由操作者在隔离 Linux VM 内手工恢复：

```bash
sudo docker exec clab-netsecops-ospf-r2 vtysh \
  -c 'configure terminal' -c 'router ospf' \
  -c 'no network 10.0.12.0/30 area 0.0.0.1' \
  -c 'network 10.0.12.0/30 area 0.0.0.0' -c 'end' &&
check_converged
```

两端均收敛后，在 Linux 采集 shell 执行 `collect_lab "$LAB_RUN/restored"`。在 NetSecOps venv 中按正常包步骤导出，把目录名替换为 `restored`，目标和设备均为 `r2`，保留 `--require-peer --expect-status normal`；按第 3 节导入到同一 r2 映射。归档操作前后的 observations、包、版本 ID、规则运行和 Findings，比较两端 running-config。恢复后应为 `no_draft`；若要确认本地 R005 为 `satisfied`，规则检查必须显式绑定恢复时双端配置（正常包导入不固定对端历史版本，不以 `no_draft` 代替 R005 结果）。

### 验收检查

- normal：`status=no_draft`；设备详情可回查提交人、原始观测、配置版本、规则结果。
- area：仅对应链路、绑定两端快照的 R005 生成 `draft`；提交/审批走既有流程。
- 同包重导：返回原导入 ID、`duplicate=true`，业务行不增长。
- 换错环境/目标设备：422、请求目标/映射目标/对端均零新增。
- 人工修改实验设备后重新采集，核对变更前后快照与规则结果。

## 5. 清理

```bash
cd "$NETSECOPS"
sudo containerlab destroy -t labs/ospf-baseline/topology.clab.yml --cleanup
# 保留 LAB_RUN 中的观测、证据包及规则结果供复审。
```
