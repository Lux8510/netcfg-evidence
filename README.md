# netcfg-evidence

**把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。**

当前进度：W1 —— 解析器 + 漂移检测 + 持久化 API + 4 条确定性规则 + 证据查询 + 20 例漂移评测（前端待做）。

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

数据库默认 `sqlite:///./netcfg.db`，可用 `NETCFG_DATABASE_URL` 指向 PostgreSQL。

## 规则与三值判定

4 条规则全部是确定性函数，不调用模型：

| ID | 规则 | 违规条件 |
| --- | --- | --- |
| R001 | 点到点 OSPF 接口必须显式设置 cost | `network point-to-point` 但没有 `ip ospf cost` |
| R002 | network 宣告必须匹配本机接口网段 | 宣告的前缀不在任何接口网段内 |
| R003 | Loopback 接口必须 passive | `lo` 有地址但不在 passive 列表 |
| R004 | router-id 必须是本机 Loopback 地址 | 不一致 |

每条规则的判定结果是 **violated / satisfied / undetermined**。当配置里存在未解析的 OSPF 相关行时，结果为 `undetermined`，**不会**被当成通过。

## 评测

```bash
make eval    # 20 个人工标注的漂移案例
```

当前结果：**20/20 精确匹配，precision 1.0000，recall 1.0000**。期望值由人工写死，不由被测实现生成。

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
evals/run_eval.py          评测脚本（precision/recall）
samples/frr/               真实实验配置样本（脱敏）
tests/                     解析与漂移测试
DESIGN.md                  完整设计草案
docs/INTERVIEW_NOTES.md    面试知识点与追问
```

## 边界

- 仅 FRR 语法子集；Cisco IOS 留待后续
- 不做全网路由仿真（与 Batfish 分工不同）
- 数据全部来自本地样本，无网络请求
