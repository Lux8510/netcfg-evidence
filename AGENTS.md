# AGENTS.md

本文件给在仓库里运行的编码/审查 Agent（如 Codex）看。默认任务：**只审查，不改源码**。

## 项目一句话

把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。
当前是 W2：解析器、漂移检测、持久化 API、4 条确定性规则、变更单与人工审批、解释层（Mock + 引用校验）、单页工作台、三套评测。

## 审查前先跑这三条

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python evals/run_eval.py
.venv/bin/python tools/check_publication.py
```

三条全过再读代码；跑不过直接作为第一条结论写出。

## 读代码顺序（约 15 分钟）

1. `DESIGN.md` — 边界与"不做什么"
2. `domain/netcfg/parser.py` — 未知行是否记录、行号是否保留
3. `domain/netcfg/drift.py` — 有无误报（无变化、仅注释变化、块顺序变化）
4. `domain/netcfg/rules.py` — 三值判定是否成立
5. `domain/netcfg/service.py` — 写操作入口、幂等、版本不可变
6. `engine/models.py` — 约束是否落在数据库层
7. `domain/netcfg/change_requests.py` — 状态机能否被绕过、审批人是否可伪造
8. `domain/netcfg/explain.py` — 引用校验有没有漏网、rejected 是否真的不影响结论
9. `frontend/src/` — 是否有未转义的用户内容、异步写回是否校验设备身份
10. `tests/` — 是否只有 happy path

## 必须质疑的五点

1. `unparsed` 里的行，会不会让本该违规的规则被判成 satisfied？
2. canonical 哈希排序后，是否存在"顺序有意义"的语法被错误归一化？
3. 同一份配置重复导入是否真的幂等？并发下呢？
4. 基线替换时，是否可能出现两条 active 记录？
5. 20 个评测案例的期望值，是否真由人工写死，而不是由实现生成？
6. 变更单能否在没有 approve 的情况下到达 applied？审批人能否写成 system/agent？
7. 解释层的校验能否被绕过：例如正文说"第 14 行"但引用指向别处，或引用多条混入一条非证据行？
8. 前端是否把用户输入直接塞进 innerHTML？是否有 XSS？
9. 页面加载时的 GET 是否真的不产生写操作？
10. 静态目录挂载在最后，是否仍可能抢占 `/api` 或 `/docs`？

## 已知限制（不要重复报）

- 只支持 FRR 语法子集，Cisco IOS 未实现
- 解释层只有确定性 Mock，尚未接入真实模型
- 账号是本地单机；没有密码重置、登录限流与多因素认证
- Docker 只由 CI 验证，本机没有运行时
- 数据库用 create_all 建表，没有迁移工具
- 删除设备会级联删除其审计事件（当前没有删除 API）
- 评测只覆盖 20 个案例，不代表真实世界准确率
- 并发测试尚未覆盖

## 本轮定向审查（W4：认证 + 新前端）

请只针对以下四块，逐条给出「确认 / 反驳」并附证据：

### A. 认证与会话安全

- Cookie 只放随机会话令牌、库里存 SHA-256：确认没有明文令牌落库
- `HttpOnly` / `SameSite=Lax` 的边界：跨站 POST 是否被浏览器拦住？同站子域呢？
- **CSRF 没有显式 token**：如果 SameSite 失效或通过同站入口，写接口能否被诱导调用？
- 会话固定：登录是否总是新建令牌并拒绝旧令牌？退出是否真的让令牌失效？
- 角色绕过：除 `/api/change-requests/{id}/(approve|reject|apply)` 外，是否还有写接口漏了角色检查？
- 无登录限流 / 无密码复杂度策略：请评估影响，不要假装已实现
- 请求体里的 `actor` 被忽略：确认审计里的操作者只能来自会话

### B. 新前端（React）的 XSS 与状态隔离

- 搜索一切 `dangerouslySetInnerHTML`、`innerHTML`、`eval`、`new Function`
- SVG 拓扑、Toast、Dialog 中的用户内容是否都经过 React 的自动转义
- 切换设备时：`drift` / `ruleCheck` / `changes` / `explanations` / `audit` 是否会短暂或持久地显示上一台设备的数据
- 异步响应竞态：快速切换设备后，旧请求返回是否还能写入状态（代码里有没有请求序号或 abort）
- 证据按钮是否始终绑定该 Finding 自己的 `config_version_id`

### C. 3D、动效与可访问性

- `<900px` 或 `prefers-reduced-motion` 时 3D 是否真的不加载
- Canvas 是否 `aria-hidden`、是否拦截鼠标事件
- 键盘可达性：Dialog 能否 ESC 关闭、焦点是否可预期；纯图标按钮是否有 `aria-label`
- 颜色对比度：`--color-muted` 在深色背景上的可读性
- 动效是否可能引发布局抖动或影响阅读

### D. 承诺与机制一致性

- 打开 `docs/CLAIMS_AUDIT.md`，对本轮新增条目（前端产物一致性、3D 开关、CSRF、限流）逐条确认或反驳
- CI 的 `npm run build && git diff --exit-code web` 是否真的能抓住"源码改了产物没更新"
- Docker 多阶段构建是否仍然"迁移后启动"、非 root、healthcheck 有效


### 附：CLAIMS_AUDIT 的「强制」条目

打开 `docs/CLAIMS_AUDIT.md`。对标记为 **强制** 的每一条（C01–C13、C15–C19、C22、C23、C25），做两件事：

1. 找到执行该约束的代码位置，说明它在哪一层（数据库 / 服务 / API / 前端）
2. 尝试用**应用自身或直接 SQL** 绕过它；绕得过去就判为「部分」并给出复现步骤

重点怀疑：

- C06 版本不可变：触发器是否覆盖 SQLite 和 PostgreSQL？删表重建后触发器还在吗？
- C15 审计只读：删除设备会级联删除审计，是否与「无法抵赖」矛盾？
- C09 证据可回查：Finding 的 `config_version_id` 是否总是等于生成它的那次运行？
- C19 解释引用：结构化引用正确时，正文语义新增是否完全不受约束？

### B. PostgreSQL 上的触发器是否真的生效

没有实例就从代码层审查并明确写「未验证」，不要假装跑过。需要检查：

- `prevent_audit_mutation()` / `prevent_config_version_update()` 的创建顺序与幂等性
- `after_create` 事件是否只在建表时执行；已有库升级时触发器是否存在
- 部分唯一索引 `one_active_baseline` 的 `postgresql_where` 写法是否正确
- `JSON` 列在 PostgreSQL 下的类型与约束

### C. 前端在「多设备 / 多版本 / 多运行」切换时是否混用身份

只读 `frontend/src/views/Dashboard.tsx` 与 `frontend/src/components/ui.tsx`，指出具体行号：

- 切换设备后，`state.findings`、`state.changeRequest`、漂移面板是否残留上一个设备的数据
- 规则证据按钮的 `data-v` 是否始终来自该条 Finding 自己的 `config_version_id`
- 解释面板是否可能展示另一个 run 的引用
- 变更单操作按钮的 `data-id` 是否可能指向已切换走的设备

## 输出要求

**不要修改源码**，只把意见写进 `review-feedback/<YYYY-MM-DD>-<agent>.md`。

每条意见按这个格式：

```markdown
### [编号] 一句话结论

- 结论：接受 / 拒绝 / 待验证
- 位置：`文件:函数` 或 `文件:行`
- 问题：为什么这是问题（要能构造出触发场景）
- 建议：最小修改方案
- 严重级别：高 / 中 / 低
```

没有具体触发场景的"建议加抽象""建议换框架"不要写；如果确实想提，必须说明它解决什么具体问题。

**定向审查的每条结论必须二选一**：

- 「确认」：附代码位置 + 你尝试过的绕过手段 + 为什么绕不过
- 「反驳」：附可复现的最小步骤（命令或配置文本）与观察到的结果

## 不要做的事

- 不要 `git commit`、不要 `git push`
- 不要改动 `samples/`、`evals/cases.json`（那是评测基准）
- 不要为了让测试通过而修改测试断言
- 不要引入新依赖
