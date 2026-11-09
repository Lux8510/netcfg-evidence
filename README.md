# netcfg-evidence

**把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。**

当前进度：W1 —— 确定性解析器 + 结构化漂移检测 + SQLite/PostgreSQL 持久化 + HTTP API（前端待做）。

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

数据库默认 `sqlite:///./netcfg.db`，可用 `NETCFG_DATABASE_URL` 指向 PostgreSQL。

## 目录

```text
domain/netcfg/parser.py    FRR 配置解析
domain/netcfg/drift.py     结构化漂移检测
domain/netcfg/service.py   业务逻辑：导入、基线、漂移、序列化
engine/db.py               数据库连接与会话
engine/models.py           设备、配置版本、基线、漂移报告
api/main.py                FastAPI 路由
samples/frr/               真实实验配置样本（脱敏）
tests/                     解析与漂移测试
DESIGN.md                  完整设计草案
docs/INTERVIEW_NOTES.md    面试知识点与追问
```

## 边界

- 仅 FRR 语法子集；Cisco IOS 留待后续
- 不做全网路由仿真（与 Batfish 分工不同）
- 数据全部来自本地样本，无网络请求
