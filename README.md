# netcfg-evidence

**把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。**

当前进度：W1 —— 解析器 + 漂移检测 + 持久化 API + 4 条确定性规则 + 三值判定 + 证据查询 + 20 例漂移评测（前端待做）。

- 不连接设备、不下发配置、不仿真全网转发
- 解析器只覆盖**明确声明**的 FRR 语法子集，未知行逐条记录，不静默丢弃
- 每个解析结果都带源文件行号，供后续 Finding 引用

## 快速开始

需要 Python 3.12。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 当前状态

- 测试 **103 项**
- 评测 **20/20**，precision / recall 1.0000
- 规则 4 条，三值判定 + not_applicable
- 变更单状态机 + 强制人工审批 + 追加式审计事件
- 解释层（Mock）+ 引用校验：解释不能改变判定
- 覆盖设备 / 版本 / 基线 / 漂移 / 规则 / Findings 的 HTTP API

## 当前能力

启动 API：

```bash
.venv/bin/python -m uvicorn api.main:app --port 8000
# 文档：http://127.0.0.1:8000/docs
```

也可以直接调用模块：

```python
from domain.netcfg.parser import parse_frr_config
from domain.netcfg.drift import diff_configs

baseline = parse_frr_config(open("samples/frr/r1.conf").read(), "r1-baseline")
current = parse_frr_config(open("samples/frr/r1.conf").read().replace("ip ospf cost 10", "ip ospf cost 20"), "r1-current")
report = diff_configs(baseline, current)
print(report.as_dict())
```

输出包含 `path`、`kind`、`old`、`new`、`old_lines`、`new_lines`，可直接作为证据渲染。

## 设计要点

| 决定 | 理由 |
| --- | --- |
| 确定性解析，不用 LLM | 判定必须可复现、可回归；LLM 只负责解释 |
| 双哈希（raw / canonical） | raw 用于文件完整性，canonical 用于内容比较 |
| 未知行记录而非丢弃 | 静默丢弃会让"没检测到问题"变成假阴性 |
| 行号随值保存 | 后续规则判定必须能指回证据 |
| 只支持声明过的语法子集 | 宁可报"无法判定"，也不猜 |

## HTTP API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| POST | `/api/devices` | 新建设备 |
| GET | `/api/devices` | 设备列表 |
| POST | `/api/devices/{id}/configs` | 导入配置（相同字节幂等返回原版本） |
| GET | `/api/devices/{id}/configs` | 版本列表 |
| GET | `/api/versions/{id}` | 版本详情（含原文、结构化字段、未解析行） |
| POST | `/api/devices/{id}/baseline` | 设置基线版本 |
| GET | `/api/devices/{id}/baseline` | 当前基线 |
| POST | `/api/devices/{id}/drift` | 计算漂移并落库 |
| GET | `/api/devices/{id}/drift` | 漂移报告历史 |
| GET | `/api/drift/{id}` | 单份漂移报告 |
| GET | `/api/rules` | 规则清单（ID、说明、来源、级别） |
| POST | `/api/devices/{id}/rules/check` | 对某版本跑规则，落库 Findings |
| GET | `/api/devices/{id}/findings` | 查询 Findings（可按 `run_id` 过滤） |
| GET | `/api/versions/{id}/lines?start=&end=` | 取原始配置行作为证据 |
| POST | `/api/devices/{id}/change-requests` | 由 violated Finding 生成变更单 |
| GET | `/api/devices/{id}/change-requests` | 变更单列表 |
| GET | `/api/change-requests/{id}` | 变更单详情（含创建时的 Finding 快照） |
| POST | `/api/change-requests/{id}/{submit,approve,reject,apply,cancel}` | 状态流转，必须填真实审批人 |
| GET | `/api/change-requests/{id}/events` | 追加式审计事件（不可修改） |
| POST | `/api/devices/{id}/explanations` | 为某次规则执行的 Finding 生成解释 |
| GET | `/api/devices/{id}/explanations` | 解释列表（含 rejected） |
| GET | `/api/explanations/{id}` | 单条解释与校验结果 |

数据库默认 `sqlite:///./netcfg.db`，可用 `NETCFG_DATABASE_URL` 指向 PostgreSQL。

## 变更单与人工审批

```text
draft -> submit -> pending_approval -> approve -> approved -> apply -> applied
                                pending_approval -> reject -> rejected
                     draft -> cancel -> cancelled
```

约束：

- 只能由 **violated** 的 Finding 生成；`undetermined` 不能变成变更单
- 变更单会保存创建时的 Finding 快照，之后 Finding 变化不影响已提交内容
- 每次流转必须填写**真实审批人**，`system` / `agent` / `bot` 一律拒绝
- 所有流转写入 `audit_events`，只追加，没有修改或删除接口
- `executed_on_device` 恒为 `false`：系统只生成变更单，不下发配置
- `apply` 只表示"人工已在维护窗口执行"，不验证设备状态

## 解释层：模型只能解释，不能判定

`MockExplainer` 是确定性替身，接口 `Explainer` 可替换成真实模型。每条解释在落库前必须通过校验：

| 校验 | 失败原因码 |
| --- | --- |
| 引用的规则必须存在 | `unknown_rule` |
| 引用的规则必须就是该 Finding 的规则 | `rule_mismatch` |
| 引用的行必须是该 Finding 的证据行 | `line_not_in_evidence` |
| 至少有一条引用 | `missing_citation` |
| 正文必须提到被引用的行号 | `line_not_mentioned` |
| 正文不能引用未声明的其他规则 | `text_cites_unlinked_rule` |
| 不能声称"已修改/已下发" | `overclaim` |
| 长度 10–2000 且无控制字符 | `text_invalid` |

校验不通过的解释**仍会保存**（`status=rejected`），但不会进入展示，也不会影响 Finding。

## 规则与三值判定

4 条规则全部是确定性函数，不调用模型：

| ID | 规则 | 违规条件 |
| --- | --- | --- |
| R001 | 点到点 OSPF 接口必须显式设置 cost | `network point-to-point` 但没有 `ip ospf cost` |
| R002 | network 宣告必须匹配本机接口网段 | 宣告的前缀不在任何接口网段内 |
| R003 | Loopback 接口必须 passive | `lo` 有地址但不在 passive 列表 |
| R004 | router-id 必须是本机 Loopback 地址 | 不一致 |

每条规则的判定结果是 **violated / satisfied / undetermined**；配置里根本没有 OSPF 时单列为 **not_applicable**。

- 存在未解析的 OSPF 相关行（包括 `no ...` 与 `router ospf <instance>`）→ `undetermined`，**不会**被当成通过
- 声明了 OSPF 但进程块无法解析 → 全部 `undetermined`
- 完全没有 OSPF → `not_applicable`，不算通过也不算违规

## 审查

```bash
make check        # 检查凭据、绝对路径、失效链接
make bundle       # 生成可独立审查的 zip（含 MANIFEST 哈希与审查请求）
make review-kit   # 打印用 Codex 审查的最短步骤
```

审查 Agent 的约定写在 [AGENTS.md](AGENTS.md)：只审查、不改源码、意见写到 `review-feedback/`。
详细审查路径见 [REVIEW_GUIDE.md](REVIEW_GUIDE.md)。

## 评测

```bash
make eval    # 漂移 20 例 + 规则判定 15 例 + 解释校验 12 例
```

当前结果：

- 漂移：**20/20 精确匹配**，precision 1.0000，recall 1.0000
- 规则判定：**15/15 精确匹配**，逐规则比较 60 个判定（含 satisfied / violated / undetermined / not_applicable）
- 解释校验：**12/12 精确匹配**，覆盖 8 种拒绝原因



期望值由人工写死，不由被测实现生成。部分案例还声明了 `expected_items`（kind、old/new、证据行号），所以把 10→20 写成 20→10 会被判失败；每条漂移还必须带证据行号。

## 目录

```text
domain/netcfg/parser.py    FRR 配置解析
domain/netcfg/drift.py     结构化漂移检测
domain/netcfg/service.py   业务逻辑：导入、基线、漂移、序列化
engine/db.py               数据库连接与会话
engine/models.py           设备、配置版本、基线、漂移报告
domain/netcfg/rules.py     4 条确定性规则与三值判定
api/main.py                FastAPI 路由
evals/build_cases.py       生成 20 个漂移案例
evals/build_rule_cases.py  生成 15 个规则判定案例
evals/run_eval.py          漂移评测（precision/recall）
evals/run_rules_eval.py    规则状态评测
evals/run_explain_eval.py  解释引用校验评测
samples/frr/               真实实验配置样本（脱敏）
tests/                     解析、漂移、规则、API、评测与审查回归测试
DESIGN.md                  完整设计草案
docs/INTERVIEW_NOTES.md    面试知识点与追问
```

## 边界

- 仅 FRR 语法子集；Cisco IOS 留待后续
- 不做全网路由仿真（与 Batfish 分工不同）
- 数据全部来自本地样本，无网络请求
