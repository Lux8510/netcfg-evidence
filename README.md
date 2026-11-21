# netcfg-evidence

**把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。**

[![CI](https://github.com/Lux8510/netcfg-evidence/actions/workflows/ci.yml/badge.svg)](https://github.com/Lux8510/netcfg-evidence/actions/workflows/ci.yml)
![Python 3.12](https://img.shields.io/badge/python-3.12-3776AB)
![React 19](https://img.shields.io/badge/react-19-61DAFB)
![tests 230](https://img.shields.io/badge/tests-230-3ECF8E)
![license MIT](https://img.shields.io/badge/license-MIT-green)

判定由确定性代码完成，模型只能解释；变更必须由人审批。系统不写入设备：只读采集仅执行白名单 `show` 命令，不下发配置、不做转发仿真。

![工作台](docs/ui-dashboard.png)

**60 秒演示：`docs/demo.mp4` · [演示脚本](docs/DEMO.md) · [架构](docs/ARCHITECTURE.md)**

## 3 条命令跑起来

需要 Python 3.12；前端产物已提交，运行不需要 Node。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn api.main:app --port 8000
```

打开 http://127.0.0.1:8000/ ，第一次先建账号：

```bash
.venv/bin/python scripts/create_user.py --username 你的名字 --role admin
```

## 它解决什么

网络排障和变更里，最贵的不是"找出问题"，而是**事后无法证明**：这条判定依据哪一行？谁批准的？改之前长什么样？

- **版本不可变**：配置只追加，数据库触发器拒绝 UPDATE/DELETE
- **判定有证据**：每条 Finding 带配置行号 + 规则 ID；未解析的 OSPF 行一律 `undetermined`，不会被当成通过
- **模型只能解释**：引用校验不过就丢弃，模型不能改变 Finding
- **变更要人批**：审批人来自登录会话；状态机 + 唯一约束 + 追加式审计
- **漂移可豁免**：绑定当前值与路径、带到期时间，到期自动恢复
- **深色 / 浅色主题**：偏好持久化，首屏无闪烁；图表与 SVG 随主题切换

## 关键数字

| 项 | 数量 |
| --- | --- |
| 单元/接口测试 | **230**（含 8 条 hypothesis 属性测试） |
| 浏览器端到端 | **22**（真实 Chrome + 真实服务进程） |
| 真实并发 | **3**（独立进程 + 多线程 HTTP） |
| 评测 | 漂移 20/20 · 规则 15/15 · 解释 17/17 |
| 规则 | 4 条本机 + 3 条链路 |
| 压测 | 300 请求 / 20 并发：887 rps，p95 34ms（SQLite WAL） |

## 目录

```text
api/                 FastAPI 路由
domain/netcfg/       解析、漂移、规则、审批、豁免、解释
engine/              数据库、迁移守卫、认证、可观测性
migrations/          Alembic 迁移
frontend/            React + Tailwind + Motion + R3F 源码
web/                 前端构建产物（已提交）
docs/                架构、演示、评测、承诺对照
tests/               单元/接口/并发/属性/浏览器测试
```

## 接入真实设备（只读 SSH，可选）

没有设备也能验证：仓库自带一个假 SSH 设备用于测试；真实设备需要你确认授权与只读账号。

1. 在服务端环境变量里放密码（接口只保存变量名，不存密码）：

```bash
export NETCFG_SSH_PASSWORD='...'
# 默认严格校验主机指纹；首次接入请登记设备指纹，或显式关闭（不建议生产）
# export NETCFG_SSH_STRICT_HOST_KEY=false
# export NETCFG_SSH_KNOWN_HOSTS=/path/to/known_hosts
# 可选：NETCFG_SSH_TIMEOUT、NETCFG_SSH_KEY_PATH
```

生产环境建议再加两道白名单：`NETCFG_SSH_ALLOWED_HOSTS`（逗号分隔的主机）与
`NETCFG_SSH_ALLOWED_CREDENTIAL_REFS`（允许使用的凭据变量名）；超出范围的请求返回 422。

2. 配置设备目标（`PUT /api/devices/{id}/target`），或在工作台「SSH 采集与变更核对」卡片填写。

3. 采集只执行白名单命令：`show running-config`、`show ip ospf neighbor`、`show ip interface brief`。
   没有任意 shell、没有配置写入路径；失败会记录采集事件且**不生成版本**。

4. 变更核对：`开始变更窗口` 采集变更前快照 → 人工在设备上变更 → `完成核对` 采集变更后快照，
   自动输出前后差异与规则结果。

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| PUT/GET | `/api/devices/{id}/target` | 配置 / 查看 SSH 目标（不含密码） |
| POST | `/api/devices/{id}/collect` | 采集一次配置并生成版本 |
| POST | `/api/devices/{id}/change-runs` | 开始变更窗口（采集变更前快照） |
| POST | `/api/change-runs/{id}/complete` | 采集变更后快照并核对 |
| GET | `/api/devices/{id}/change-runs` | 变更核对历史 |

边界：SSH 采集只对**假设备**做过自动化验证，没有在真机跑过；分页/提示符处理只覆盖常见情况，真机需要按厂商核对。
变更窗口会保存目标快照，窗口期间目标变化会拒绝核对（`TARGET_CHANGED`）；
采集失败会持久化为 `failed` 事件与窗口，且不生成版本。

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
- 未登录访问 `/api/*` 返回 401，前端显示登录视图（单页应用，不再跳转独立页面）

## 当前能力

启动（同一进程同时提供 API 与网页）：

```bash
.venv/bin/python -m uvicorn api.main:app --port 8000
# 网页：http://127.0.0.1:8000/
# 接口文档：http://127.0.0.1:8000/docs
```

构建产物已提交在 `web/`，所以运行不需要 Node；修改前端后执行 `make ui` 重建。

![工作台](docs/ui-dashboard.png)

![拓扑](docs/ui-topology.png)

![证据脊柱](docs/ui-evidence-spine.png)

![审批链](docs/ui-custody-chain.png)

![登录](docs/ui-login.png)

支持深色 / 浅色主题切换，偏好存在 localStorage，首屏由内联脚本设置避免闪烁（浅色版见 [docs/ui-dashboard-light.png](docs/ui-dashboard-light.png)）。

**三栏工作台**：左栏版本与 SSH 采集，中栏漂移与规则证据，右栏拓扑与审计；变更单与审批链在底部横跨全宽。

**证据脊柱**：漂移项和规则证据左侧有常驻行号槽；悬停某一行，另一张卡里引用同一接口/同一行的条目会同时亮起，点击行号直接打开原文。设计说明见 [docs/UI_SPEC.md](docs/UI_SPEC.md)。

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
| 审计哈希链 | 每条审计事件写入 `prev_hash + entry_hash`；`/api/audit/verify` 与 CLI 可验证 |
| Finding 不可改写 | `BEFORE UPDATE` 触发器 |
| 解释记录不可改写 | `BEFORE UPDATE` 触发器 |
| 变更单的证据快照不可改 | `BEFORE UPDATE` 比较 `finding_ids`/`findings_snapshot` |
| 变更单必须引用同设备、violated 的 Finding | `BEFORE INSERT` 子查询校验 |
| 快照条数必须等于 Finding 条数 | `BEFORE INSERT` 比较 JSON 数组长度 |
| 每设备最多一条 active 基线 | 部分唯一索引，启动时幂等补建 |
| 未知/未解析的 OSPF 行 | 任何未覆盖规则一律 `undetermined` |

副作用：**删除设备会被触发器阻止**（级联会删除版本与审计）。当前没有删除 API，这是刻意选择。

审计哈希链：

```bash
.venv/bin/python scripts/verify_audit_chain.py
# 或 GET /api/audit/verify
```

每条审计事件按 `seq → prev_hash → entry_hash` 串起来，哈希覆盖事件字段、快照与时间。  
改任意一条、插一条链外事件、或删一条，都会在验证时定位到第一个断点。  
边界：拥有 DDL 权限的库所有者仍可重建整条链；它防的是“悄悄改一条”，不是“重写整个数据库”。

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

## 与 NetSecOps Agent 联动

NetSecOps Agent 诊断结束后可以导出带哈希的证据包，netcfg 负责“第二次独立判定”：

```bash
# NetSecOps 侧：--device 必须等于快照来源节点
python labs/ospf-baseline/handoff.py --trace trace.json --device r2 --target r2 --out handoff.json

# netcfg 侧：显式指定目标设备（只匹配已登记设备，不自动创建）
# 目标设备名必须等于包内节点（snapshot.node），否则拒绝
python scripts/import_diagnosis.py --bundle handoff.json --username 何深 --device-id <uuid>
```

导入通过认证的 `POST /api/devices/{device_id}/diagnosis-imports` 在**一个事务**里完成：

1. 校验 bundle 哈希、trace_id、快照节点/目标、观测 content_sha256、双端快照，并核对目标设备名与包内节点一致（两级摘要错、未知 schema、节点或目标不一致、缺失快照引用、展示文本与摘要不一致、设备错绑均拒绝）；
2. 复用配置导入、规则检查与变更单服务；Area 场景只有在两端设备与接口都已登记链路时才导入**对端快照**，并标记为历史证据（不进入默认最新版本列表），规则复算固定到该不可变版本；普通上传/采集与历史同哈希可共存，普通上传仍会成为当前版本；
3. 只对**适用且绑定到包所指链路与双方版本**的本地 violated Finding 创建草稿，修复方案由本地 Finding 推导，外部建议命令存入导入记录供查看，不进入草稿；无对端快照或身份不符时仅存证据；
4. 正常、采集失败、证据不足、无合格 Finding 时保存证据与规则结果，草稿为空；
5. 同 bundle 或同 trace 重复导入返回原记录（`duplicate=true`），不新增业务行；
6. 始终不移动现有基线。

设备详情提供证据包上传入口（JSON 文件），目标固定为当前选中的已登记设备，并按「已生成草稿 / 仅存证 / 重复导入」展示结果；校验失败直接显示服务端原因。也可通过 `GET /api/devices/{id}/diagnosis-imports` 追溯提交人、提交时间、完整 bundle 哈希、trace_id、原始证据摘要、config_version_id、rule_run_id 与可选 change_request_id。外部诊断结论只写入理由，**不改变判定**；两个项目共享证据，但各自保留判定权。

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

## 多模型对比

同一批 15 条已标注 Finding，比较 `mock` / `deepseek-chat` / `deepseek-reasoner`：

| 模型 | 结构接受 | 引用完全正确 | p50 | p95 | 估算成本 |
| --- | --- | --- | --- | --- | --- |
| mock | 15/15 | 15/15 | 0 ms | 0 ms | ¥0 |
| deepseek-chat | 15/15 | 15/15 | 1323 ms | 1493 ms | ¥0.0204 |
| deepseek-reasoner | 15/15 | 15/15 | 2007 ms | 2729 ms | ¥0.0671 |

结论：结构指标已饱和，推理档没有带来收益却贵约 3 倍、慢约 1.8 倍，因此解释层默认 `deepseek-chat`。
对比过程中还发现并修复了两个校验器缺陷（跨设备规则 ID 被误判 unknown_rule；否定句被误判 overclaim）。
方法、边界与复现见 [模型对比报告](docs/model-comparison.md)。

```bash
make compare
```

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

## 跨仓契约测试

```bash
make contract   # 需要可访问的 ../netsecops-agent checkout
```

用真实 CLI 跑通一条闭环：NetSecOps `handoff.py` 导出，netcfg `import_diagnosis.py` 导入到一个临时 SQLite + 真实 uvicorn。覆盖：

1. normal：存证、无草稿、同包重导返回原 ID；
2. area-mismatch：登记链路后由本地 R005 生成草稿、重导幂等；
3. insufficient/采集失败：仅存证；
4. 被篡改的证据包：CLI 非零退出且不新增导入记录。

报告写入 `contract-report.json`，记录两个仓库的 commit 与 bundle schema。CI 的 `contract` job 会 checkout 两个仓库并运行同一脚本。

PostgreSQL 验收（CI service container）：

```bash
CONTRACT_DATABASE_URL=postgresql+psycopg://postgres:postgres@127.0.0.1:5432/netcfg \
  python tools/contract_postgres.py --netsecops ../netsecops-agent
```

覆盖：带数据迁移、同哈希历史/当前共存、并发导入去重、草稿后失败回滚。报告为 `contract-postgres-report.json`。

## 浏览器端到端

```bash
make e2e    # 需要本机 Chrome 与 playwright；CI 无浏览器时会自动跳过
```

22 条真实浏览器用例（三栏工作台）：

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
13. 主题切换写入 localStorage，刷新后保持
14. SSH 变更核对：保存目标 → 采集前快照 → 完成核对 → 展示差异
15. 采集中切换设备，旧响应不会写回新设备
16. 悬停证据行号，另一张卡的关联条目同步高亮；点击行号打开原文
17. 设备详情展示诊断导入记录、原始观测与配置原文
18. 从诊断详情打开当次规则结果与证据行
19. 上传正常包：仅存证，不生成草稿
20. 上传 Area 不一致包：生成本地 R005 草稿
21. 上传设备不匹配包：展示服务端拒绝原因，零新增记录
22. 同包重复上传：返回原导入记录

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
make eval    # 漂移 20 例 + 规则判定 15 例 + 解释校验 17 例
```

当前结果：

- 漂移：**20/20 精确匹配**，precision 1.0000，recall 1.0000
- 规则判定：**15/15 精确匹配**，逐规则比较 60 个判定（含 satisfied / violated / undetermined / not_applicable）
- 解释校验：**16/16 精确匹配**，覆盖 10 种拒绝原因



期望值由人工写死，不由被测实现生成。部分案例还声明了 `expected_items`（kind、old/new、证据行号），所以把 10→20 写成 20→10 会被判失败；每条漂移还必须带证据行号。

## 承诺 vs 机制

所有对外承诺逐条对照代码与测试，见 [docs/CLAIMS_AUDIT.md](docs/CLAIMS_AUDIT.md)。
每条标注「强制 / 部分 / 未实现」，包括 CSRF、登录限流、前端竞态与焦点管理这些容易被说大的地方。

## 边界

- 仅 FRR 语法子集；Cisco IOS 留待后续
- 不做全网路由仿真（与 Batfish 分工不同）
- 数据全部来自本地样本，无网络请求
