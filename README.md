# netcfg-evidence

**把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。**

当前进度：W1–W2 —— 解析器 + 漂移检测 + 持久化 API + 4 条确定性规则 + 三值判定 + 变更单审批 + 解释层 + 三套评测 + 网页工作台。

- 不连接设备、不下发配置、不仿真全网转发
- 解析器只覆盖**明确声明**的 FRR 语法子集；除少量显式忽略的配置块（如 `line vty`）外，未知行逐条记录，不静默丢弃
- 每个解析结果都带源文件行号，供后续 Finding 引用

## 快速开始

需要 Python 3.12。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 当前状态

- 单元/接口测试 **180 项**（含 8 条 hypothesis 属性测试）
- 真实并发 **3 项**（独立服务进程 + 多线程 HTTP）
- 浏览器端到端 **12 项**（真实 Chrome + 真实服务进程）
- 评测 **20/20**，precision / recall 1.0000
- 规则 4 条本机 + 3 条链路，三值判定 + not_applicable
- 变更单状态机 + 强制人工审批 + 追加式审计事件
- 解释层：Mock 或 OpenAI 兼容真实模型 + 引用校验，解释不能改变判定
- 覆盖设备 / 版本 / 基线 / 漂移 / 规则 / Findings 的 HTTP API

## 容器部署

```bash
docker compose up --build
# 首次需要建账号：
docker compose exec app python scripts/create_user.py --username 你的名字 --role admin
```

镜像以非 root 运行，启动时先 `alembic upgrade head` 再起服务，健康检查打 `/api/health`。
数据卷 `./data` 持久化 SQLite；要换 PostgreSQL 就改 `NETCFG_DATABASE_URL` 并在镜像里补 `psycopg`。

本机没有 Docker，**镜像由 CI 构建并起容器验证**（见 `.github/workflows/ci.yml` 的 `container` job），本地未运行。

## 可观测性

- 每个请求返回 `X-Request-ID`（也可由客户端传入），日志是一行一个 JSON
- `GET /api/metrics`（需登录）返回进程内计数：请求数、4xx/5xx、平均/最慢耗时、Top 路径、模型调用次数与 token
- 计数是进程内的，重启清零；定位问题是够的，不做持久化指标存储

## 本地账号与角色

应用默认要求登录（Cookie 里只放随机会话令牌，数据库存 SHA-256）：

```bash
make user USER=你的名字 ROLE=admin        # 密码从 NETCFG_USER_PASSWORD 或交互输入
make user USER=小王 ROLE=editor
make user USER=老李 ROLE=approver
```

| 角色 | 能做什么 |
| --- | --- |
| `editor` | 读全部；导入配置、设基线、跑规则、生成变更单并提交；**不能批准/拒绝/标记执行** |
| `approver` | 读全部；批准、拒绝、标记已执行；**不能改配置或登记数据** |
| `admin` | 两者都做（本地单人演示方便） |

- 审批人身份来自**登录会话**，请求体里的 `actor` 字段被忽略
- 会话默认 12 小时，Cookie `HttpOnly` + `SameSite=Lax`（HTTPS 部署设 `NETCFG_COOKIE_SECURE=true`）
- 再次登录会废止被替换的旧会话；写请求校验 `Origin`/`Referer` 与 Host 一致（SameSite 之外的纵深防御）
- 登录失败按「账号 + 来源 IP」计数，超过 `NETCFG_LOGIN_MAX_FAILURES`（默认 5）返回 429；密码至少 10 位并拒绝常见弱口令
- 未登录访问 `/api/*` 返回 401，前端跳转 `/login.html`

## 当前能力

启动（同一进程同时提供 API 与网页）：

```bash
.venv/bin/python -m uvicorn api.main:app --port 8000
# 网页：http://127.0.0.1:8000/
# 接口文档：http://127.0.0.1:8000/docs
```

![工作台](docs/ui-dashboard.png)

![拓扑](docs/ui-topology.png)

![拓扑](docs/ui-topology.png)

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
| POST | `/api/auth/login` | 登录（设置会话 Cookie） |
| POST | `/api/auth/logout` | 退出 |
| GET | `/api/auth/me` | 当前用户与角色 |
| POST | `/api/devices` | 新建设备 |
| GET | `/api/devices` | 设备列表 |
| POST | `/api/devices/{id}/configs` | 导入配置（相同字节幂等返回原版本） |
| GET | `/api/devices/{id}/configs` | 版本列表 |
| GET | `/api/versions/{id}` | 版本详情（含原文、结构化字段、未解析行） |
| POST | `/api/devices/{id}/baseline` | 设置基线版本 |
| GET | `/api/devices/{id}/baseline` | 当前基线 |
| POST | `/api/devices/{id}/drift` | 计算漂移并落库 |
| POST | `/api/devices/{id}/drift/exemptions` | 为某个漂移项登记有时限的豁免 |
| GET | `/api/devices/{id}/drift/exemptions` | 豁免列表（含过期/撤销状态） |
| POST | `/api/exemptions/{id}/revoke` | 撤销豁免 |
| GET | `/api/devices/{id}/drift` | 漂移报告历史 |
| GET | `/api/drift/{id}` | 单份漂移报告 |
| GET | `/api/rules` | 规则清单（ID、说明、来源、级别） |
| POST | `/api/devices/{id}/rules/check` | 对某版本跑规则，落库 Findings |
| GET | `/api/devices/{id}/findings` | 查询 Findings（可按 `run_id` 过滤） |
| GET | `/api/devices/{id}/rules/runs` | 每次规则执行的摘要（含零 Finding 的运行） |
| POST | `/api/links` | 登记两台设备之间的链路（接口级） |
| GET | `/api/links?device_id=` | 链路列表 |
| DELETE | `/api/links/{id}` | 删除链路 |
| GET | `/api/versions/{id}/lines?start=&end=` | 取原始配置行作为证据 |
| POST | `/api/devices/{id}/change-requests` | 由 violated Finding 生成变更单 |
| GET | `/api/devices/{id}/change-requests` | 变更单列表 |
| GET | `/api/change-requests/{id}` | 变更单详情（含创建时的 Finding 快照） |
| POST | `/api/change-requests/{id}/{submit,approve,reject,apply,cancel}` | 状态流转，必须填真实审批人 |
| GET | `/api/change-requests/{id}/events` | 追加式审计事件（不可修改） |
| POST | `/api/devices/{id}/explanations` | 为某次规则执行的 Finding 生成解释 |
| GET | `/api/devices/{id}/explanations` | 解释列表（含 rejected） |
| GET | `/api/explanations/{id}` | 单条解释与校验结果 |

数据库默认 `sqlite:///./netcfg.db`，可用 `NETCFG_DATABASE_URL` 指向 PostgreSQL（模型与触发器按 PG 语法编写，但本机无实例，**未实测**）。

### 迁移

```bash
make migrate    # alembic upgrade head
make revision m="add column"   # 生成新迁移
```

- 新库：`make migrate` 建表
- 旧库（由 `create_all` 建的）：`python -m alembic stamp head` 标记为当前版本，之后正常升级
- 测试会执行 `alembic upgrade head` 并用 `alembic check` 断言迁移与模型无漂移

## 数据库层保障

以下约束由触发器/索引执行，直接写 SQL 也无法绕过（SQLite 实测；PostgreSQL 由审查方在 17.4 上核对过行级行为，TRUNCATE 与角色权限未在本地验证）：

| 保障 | 机制 |
| --- | --- |
| 配置版本只追加 | `BEFORE UPDATE/DELETE` 触发器 |
| 审计事件只追加 | `BEFORE UPDATE/DELETE`（PostgreSQL 另有 `BEFORE TRUNCATE`） |
| Finding 不可改写 | `BEFORE UPDATE` 触发器 |
| 解释记录不可改写 | `BEFORE UPDATE` 触发器 |
| 变更单的证据快照不可改 | `BEFORE UPDATE` 比较 `finding_ids`/`findings_snapshot` |
| 变更单必须引用同设备、violated 的 Finding | `BEFORE INSERT` 子查询校验 |
| 快照条数必须等于 Finding 条数 | `BEFORE INSERT` 比较 JSON 数组长度 |
| 每设备最多一条 active 基线 | 部分唯一索引，启动时幂等补建 |
| 未知/未解析的 OSPF 行 | 任何未覆盖规则一律 `undetermined` |

副作用：**删除设备会被触发器阻止**（级联会删除版本与审计）。当前没有删除 API，这是刻意选择。

## 变更单与人工审批

```text
draft -> submit -> pending_approval -> approve -> approved -> apply -> applied
                                pending_approval -> reject -> rejected
                     draft -> cancel -> cancelled
```

约束：

- 只能由 **violated** 的 Finding 生成；`undetermined` 不能变成变更单
- 变更单会保存创建时的 Finding 快照，之后 Finding 变化不影响已提交内容
- 每次流转必须填写审批人；常见系统/Agent 标识（`system`/`agent`/`bot`/`model` 等，含全角写法）会被拒绝。这是启发式校验，不构成身份认证
- 所有流转写入 `audit_events`：HTTP 层没有修改接口，数据库层用触发器拒绝 UPDATE/DELETE
- 审批人是 Compare-And-Set 更新：两个相反的审批并发时只有一个成功，失败方返回 409 且不写事件
- 审批人名称先做 Unicode NFKC 规范化，全角 `ＡＧＥＮＴ` 也会被拒绝
- `executed_on_device` 恒为 `false`：系统只生成变更单，不下发配置
- `apply` 只表示"人工已在维护窗口执行"，不验证设备状态

## 漂移豁免（有时限）

真实运维里总有"这条偏差是已知的、暂时保留"的情况。豁免必须：

- 绑定**具体漂移项 + 当前值哈希**：同一路径以后变成别的值，不会自动被旧豁免覆盖
- 有**到期时间**（1–365 天），到期后自动恢复为待处理，不需要后台任务
- 有**原因和登记人**，撤销也要填原因，两者都写入审计事件
- 同一漂移项只能有一个生效中的豁免

漂移报告返回时附 `summary.exempted` 与 `summary.actionable`，界面显示"豁免/待处理"两个数字。

## 链路规则（跨设备）

登记两台设备的接口链路后，规则检查可以带上 `include_cross=true`：

| ID | 规则 | 判定依据 |
| --- | --- | --- |
| R005 | 链路两端 OSPF area 必须一致 | 两端接口/network 语句推导出的 area 不同 |
| R006 | 同一地址不能出现在两台设备上 | 两台设备上出现完全相同的 IPv4 地址 |
| R007 | 链路两端地址必须在同一网段 | 两端接口地址的网段没有交集 |

- 对端设备没有配置版本 → `undetermined`，不猜
- 没有登记链路 → 三条链路规则计为 `not_applicable`，不算通过
- 证据里带 `version_id`，界面会分别打开本端与对端的原文

界面左侧可以登记/删除链路；规则页勾选「包含链路规则」后一起执行。

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
| 正文提到的行号集合必须与引用集合一致 | `line_mismatch` |
| 不能声称"已修改/已下发" | `overclaim` |
| 长度 10–2000 且无控制字符 | `text_invalid` |
| 模型输出不是约定 JSON | `invalid_response` |
| 模型请求失败或返回非 200 | `provider_error` |

校验不通过的解释**仍会保存**（`status=rejected`），前端以红色标记展示以便审计，但**不会影响 Finding**。
引用校验只覆盖"引用结构 + 行号一致性"，不校验文字里的语义新增——这一点在 [承诺自查](docs/CLAIMS_AUDIT.md) 里明确列出。

## 接真实模型

解释层通过一个可替换的 `Explainer` 接口工作。默认 `mock`，不需要任何密钥：

```bash
# .env（或直接导出环境变量）
EXPLAIN_PROVIDER=openai          # 或 deepseek，均走 OpenAI 兼容协议
EXPLAIN_BASE_URL=https://api.deepseek.com/v1
EXPLAIN_MODEL=deepseek-chat
EXPLAIN_API_KEY=sk-...           # 也接受 DEEPSEEK_API_KEY / OPENAI_API_KEY
EXPLAIN_TIMEOUT=20
```

行为约定：

- 选了真实模型但没有密钥 → 返回 `409 MODEL_NOT_CONFIGURED`，**不会静默退回 Mock 假装成功**
- 模型输出必须是 `{"text": ..., "citations": [{"rule_id": ..., "lines": [...]}]}`
- 输出不是合法 JSON、缺字段、HTTP 非 200、网络失败 → 记为 `rejected`（`invalid_response` / `provider_error`），不采用
- 即使模型输出结构合法，仍要通过与 Mock 相同的引用校验
- 错误信息与落库文本不包含密钥

已在 2026-11-09 用真实 DeepSeek（`deepseek-chat`）验证一次：解释被校验器接受，token 用量 231 + 74。密钥只放在本机 `.env`（已 gitignore），测试会强制 `EXPLAIN_PROVIDER=mock`，不会调用真实接口。

冒烟检查（不写数据库、不打印密钥）：

```bash
make model-check
```

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

## 属性测试与压测

```bash
make prop    # hypothesis：解析幂等、注释/顺序不变性、diff 自反与路径对称
make load    # 150 请求 / 15 并发，0 错误且 p95 有阈值
```

属性测试抓到过一个真实错误假设：先 `no ip ospf cost` 再 `ip ospf cost 10`，最终 cost 是 10 而不是"否定永远生效"。

压测脚本可直接对演示实例跑：

```bash
python scripts/load.py --base http://127.0.0.1:8020 --requests 300 --concurrency 20 \
  --username 何深 --password '...'
```

本机实测（300 请求 / 20 并发，SQLite WAL）：**887 rps，p50 20.6ms，p95 34.1ms，0 错误**。

## 真实并发

```bash
make concurrency
```

3 条用例，全部是**独立 uvicorn 进程 + 多线程 HTTP**（不是模拟竞赛）：

1. 8 个并发导入同一份配置 → 全部 201、同一个版本 ID、库里只有一条
2. 两个并发基线切换 → 不出现 500，最终 active 恰好一条
3. 并发批准与拒绝 → 一个 200、一个 409，审计里只有一条决定

## 浏览器端到端

```bash
make e2e    # 需要本机 Chrome 与 playwright；CI 无浏览器时会自动跳过
```

12 条真实浏览器用例（Bento 工作台）：

1. 导入 → 设基线 → 比对 → 并排 diff 标出 cost 10 与 cost 30
2. 规则违规 → 生成变更单 → 提交 → 批准 → 审计事件
3. 切换设备后，上一个设备的 Finding 与未提交表单必须清空
4. 生成解释后，列表显示规则 ID 与引用
5. 切换设备时，延迟返回的旧变更单不会渲染
6. 设备名与变更单标题里的 `<img onerror>` 不会被当作 HTML 执行
7. 拓扑卡片按登记的链路画出节点，点击节点切换设备
8. 审计卡片列出变更单事件
9. 豁免可从界面登记与撤销
10. 延迟返回的旧设备响应不会覆盖新设备
11. 跨设备 Finding 的「对端证据」打开对端版本原文
12. 窄窗口下 3D 背景自动卸载；弹窗焦点圈定、拓扑节点可键盘操作

单元测试默认排除 `e2e`（`pyproject.toml` 的 `addopts`），所以 `pytest` 仍然是秒级；`make e2e` 单独跑浏览器用例。

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
make eval    # 漂移 20 例 + 规则判定 15 例 + 解释校验 16 例
```

当前结果：

- 漂移：**20/20 精确匹配**，precision 1.0000，recall 1.0000
- 规则判定：**15/15 精确匹配**，逐规则比较 60 个判定（含 satisfied / violated / undetermined / not_applicable）
- 解释校验：**16/16 精确匹配**，覆盖 10 种拒绝原因



期望值由人工写死，不由被测实现生成。部分案例还声明了 `expected_items`（kind、old/new、证据行号），所以把 10→20 写成 20→10 会被判失败；每条漂移还必须带证据行号。

## 承诺 vs 机制

所有对外承诺逐条对照代码与测试，见 [docs/CLAIMS_AUDIT.md](docs/CLAIMS_AUDIT.md)：
11 条由机制强制、4 条部分强制、0 条纯文案；发现的 3 处措辞过宽已收窄。

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
tests/test_e2e_ui.py       浏览器端到端（真实服务进程 + Chrome）
tests/test_concurrency.py  真实并发（多线程 HTTP）
migrations/                Alembic 迁移
DESIGN.md                  完整设计草案
docs/CLAIMS_AUDIT.md      承诺与机制对照表
docs/INTERVIEW_NOTES.md    面试知识点与追问
```

## 边界

- 仅 FRR 语法子集；Cisco IOS 留待后续
- 不做全网路由仿真（与 Batfish 分工不同）
- 数据全部来自本地样本，无网络请求
