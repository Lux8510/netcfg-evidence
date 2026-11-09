# 承诺 vs 机制 自查（2026-11-09）

做法：把 README / DESIGN 里写下的每一条"保证"逐条对照代码与测试，确认它是**机制强制**、**部分强制**，还是**只是文案**。

结论先行：**11 条已由机制强制，4 条部分强制，0 条纯文案；另有 3 条已修正措辞。**

| # | 承诺 | 出处 | 机制 | 结论 | 证据 |
| --- | --- | --- | --- | --- | --- |
| C01 | 不连接设备、不下发配置 | README 顶部 | 代码里没有任何 SSH/SNMP/subprocess/socket 入口；AST 扫描测试守住 | **强制** | `tests/test_claims.py::test_claim_never_connects_to_devices` |
| C02 | 不仿真全网转发 | README 顶部 | 只有解析 + 规则，无转发模型 | **强制** | 目录结构与依赖清单 |
| C03 | 未知行逐条记录，不静默丢弃 | README 顶部 | `unparsed` + 行号；显式忽略块（`line vty` 等）除外 | **部分**（见下） | `tests/test_parser.py` 两条未知行用例 |
| C04 | 每个解析值带源文件行号 | README 顶部 | `Interface.address_lines`、`OspfInterface.lines`、`hostname_line` | **强制** | `tests/test_drift.py` 证据行号用例 |
| C05 | 双哈希（raw / canonical） | README 设计要点 | 原始字节哈希 + 规范化哈希 | **强制** | `tests/test_parser.py::test_raw_hash_changes_but_canonical_hash_is_stable` |
| C06 | 版本不可变、只追加 | README / DESIGN | 无更新 API；SQLite/PostgreSQL 触发器拒绝 UPDATE | **强制** | `tests/test_claims.py::test_claim_config_versions_are_append_only` |
| C07 | 相同字节重复导入幂等 | README API 表 | `(device_id, raw_sha256)` 唯一约束 + 冲突回读 | **强制** | `tests/test_parser...`/`test_review_regressions.py` |
| C08 | 单一 active 基线 | README | 部分唯一索引 `one_active_baseline` + 冲突返回 409 | **强制**（SQLite 实测） | `tests/test_review_regressions.py` |
| C09 | 每条判定能指回配置行与规则 | README 首句 | `Finding.evidence` 落库、`/versions/{id}/lines` 回查 | **强制** | `tests/test_claims.py::test_claim_rule_results_bind_to_their_own_version` |
| C10 | 未解析的 OSPF 行不会被当成通过 | README 规则节 | `undetermined` 三值判定 | **强制** | `tests/test_review_regressions.py` |
| C11 | 没有 OSPF 时单列 not_applicable | README 规则节 | `rules_not_applicable` 计数 | **强制** | `tests/test_rules.py` |
| C12 | 变更单只能由 violated Finding 生成 | README 审批节 | `_device_findings` 校验状态 | **强制** | `tests/test_change_requests.py` |
| C13 | 变更单保存创建时的 Finding 快照 | README 审批节 | `findings_snapshot` 落库 | **强制** | `tests/test_change_requests.py` |
| C14 | 审批人必须是真实姓名 | README 审批节 | 必填 + 常见系统标识黑名单 + NFKC 规范化 | **部分**（黑名单是启发式） | `tests/test_review_w2.py` |
| C15 | 审计只追加，不可修改 | README 审批节 | SQLite/PostgreSQL 触发器拒绝 UPDATE/DELETE | **强制**（PostgreSQL 未实测） | `tests/test_review_w2.py::test_audit_events_cannot_be_updated_or_deleted` |
| C16 | 并发审批只有一个成功 | README 审批节 | Compare-And-Set 条件更新 | **强制** | `tests/test_review_w2.py::test_concurrent_approve_and_reject_leave_one_event` |
| C17 | `executed_on_device` 恒为 false | README 审批节 | 响应常量，无写入设备代码 | **强制** | `tests/test_change_requests.py` |
| C18 | 解释不能改变判定 | README 解释节 | 解释独立表；Finding 不受影响 | **强制** | `tests/test_claims.py::test_claim_explanation_never_changes_the_verdict` |
| C19 | 解释必须引用规则与证据行 | README 解释节 | 8 类结构校验 + 行号集合一致 | **强制** | `evals/run_explain_eval.py` 15/15 |
| C20 | 解释"不会进入展示" | README 解释节 | 实际列表接口会返回 rejected（带标记） | **文案错误，已修正** | 见下 |
| C21 | 解释只解释、不新增事实 | README 解释节 | 只校验引用，不校验语义新增 | **部分**（已修正措辞） | 见下 |
| C22 | 页面加载不产生写操作 | W2 审查后 | 加载只读历史，计算需显式点击 | **强制**（前端） | `web/app.js:loadDrift/loadFindings` |
| C23 | 静态挂载不抢占 API/文档 | README API 节 | 挂载顺序 + 回归测试 | **强制** | `tests/test_review_w2.py::test_docs_and_openapi_stay_available` |
| C24 | 支持 PostgreSQL | README | 模型层使用 JSON + 部分索引；**未在真实实例验证** | **部分** | 无 PostgreSQL 集成测试 |
| C26 | 解释层可替换为真实模型 | README | `Explainer` 协议 + OpenAI 兼容适配器 + 环境变量；2026-11-09 真实 DeepSeek 调用通过 | **强制（已实测一次）** | `scripts/check_model.py`、`tests/test_providers.py` |
| C27 | 未配置密钥时不会静默退回 Mock | README | `build_explainer` 抛 409 | **强制** | `tests/test_providers.py::test_build_explainer_defaults_to_mock_and_refuses_missing_key` |
| C28 | 存在数据库里的旧库也会补上追加式触发器 | README（隐含） | 启动时 `ensure_append_only` 幂等执行 | **强制** | `tests/test_bootstrap.py` |
| C29 | 切换设备不会残留上一个设备的状态 | README | `refreshDevice` 重置 findings/表单 + E2E 用例 | **强制** | `tests/test_e2e_ui.py::test_switching_device_clears_previous_findings` |
| C30 | 两端证据按钮分别打开对应版本原文 | README | 漂移响应含 `baseline_version_id` + E2E 断言 | **强制** | `tests/test_e2e_ui.py::test_import_set_baseline_and_inspect_drift_evidence` |
| C25 | 评测期望值独立于实现 | README 评测节 | 期望值在生成脚本中手写，运行时不调用被测实现 | **强制**（代码层面） | `evals/build_*.py`、`tests/test_eval.py` |

## W3 复核后的状态变化（2026-11-09）

第二轮定向审查（25 条「强制」逐条确认/反驳）后，以下条目发生变化：

| 条目 | 原状态 | 现状态 | 机制 |
| --- | --- | --- | --- |
| C04 每个解析值带行号 | 强制 | **强制（已修）** | `description_line` 单独记录，漂移证据指向该行 |
| C06 版本只追加 | 强制 | **强制（已补 DELETE）** | 新增 `config_versions_no_delete`；代价是删除设备被阻止 |
| C09 证据与运行版本一致 | 强制 | **部分** | API 路径已修（制表符 router ospf 也有证据）；数据库可把 Finding 指向别的版本（外键不校验同一运行） |
| C10 未解析 OSPF 行不算通过 | 强制 | **强制（已修）** | 任何未覆盖规则在存在未解析 OSPF 行时一律 `undetermined` |
| C12 只能由 violated 生成 | 强制 | **强制（已下沉）** | `BEFORE INSERT` 子查询校验同设备且 status=violated |
| C13 保存创建时快照 | 强制 | **部分** | 快照不可更新、条数必须一致；但直接 INSERT 可写内容不实的快照 |
| C15 审计只追加 | 强制 | **部分** | 行级 UPDATE/DELETE 已拒绝，新增 PostgreSQL `BEFORE TRUNCATE`；库所有者仍可 DROP 触发器 |
| C19 解释行号受引用约束 | 强制 | **强制（已修）** | 中文数字行号无法归一化 → `line_not_normalized` |
| C29 切换设备不残留 | 强制 | **强制（已补测试）** | 请求序号 + 设备身份双校验；新增延迟响应的 E2E 用例 |
| B02 部分索引可恢复 | — | **强制（已修）** | 启动时 `CREATE UNIQUE INDEX IF NOT EXISTS`；有重复 active 时拒绝启动 |
| 新增：结构可升级 | — | **强制** | Alembic 迁移 + `alembic check` 断言无漂移 | `tests/test_migrations.py` |
| 新增：真实并发不产生 500 | — | **强制** | 独立服务进程 + 8 并发导入 / 基线切换 / 审批竞争 | `tests/test_concurrency.py` |
| 新增：提示注入不改变判定 | — | **强制（结构层）** | 配置中的指令不改变规则；无引用的模型输出被拒 | `tests/test_providers.py` |
| 新增：XSS 不执行 | — | **强制** | `<img onerror>` 在设备名与变更单标题中只显示为文本 | `tests/test_e2e_ui.py` |
| 新增：测试不调用真实模型 | — | **强制** | `tests/conftest.py` 强制 `EXPLAIN_PROVIDER=mock` | 全部测试 |
| 新增：token 用量可追溯 | — | **强制** | `explanations.usage` 列 + 迁移 2 + 界面展示 | `tests/test_providers.py` |
| 新增：链路两端 area 一致 | — | **强制** | R005 跨设备规则 + 证据带 version_id | `tests/test_cross_rules.py` |
| 新增：跨设备重复地址 | — | **强制** | R006 跨设备规则 | `tests/test_cross_rules.py` |
| 新增：链路两端同网段 | — | **强制** | R007 跨设备规则 | `tests/test_cross_rules.py` |
| 新增：豁免只覆盖登记时的值 | — | **强制** | 豁免绑定 path + value hash | `tests/test_exemptions.py` |
| 新增：豁免到期自动失效 | — | **强制** | 读取时按 expires_at 过滤，无需后台任务 | `tests/test_exemptions.py` |
| 新增：豁免可撤销且留审计 | — | **强制** | 撤销写 AuditEvent，库内只追加 | `tests/test_exemptions.py` |
| 新增：接口需要登录 | — | **强制** | 中间件校验会话 Cookie，401 拦截 | `tests/test_auth.py` |
| 新增：审批需要 approver 角色 | — | **强制** | 路由级角色检查，editor 返回 403 | `tests/test_auth.py` |
| 新增：审批人来自会话而非请求体 | — | **强制** | 请求体 actor 被忽略，审计取会话用户 | `tests/test_auth.py` |
| 新增：请求可追踪 | — | **强制** | X-Request-ID + 一行 JSON 日志 | `tests/test_observability.py` |
| 新增：模型调用可计数 | — | **强制** | 成功/失败、token、延迟计数 | `tests/test_observability.py` |
| 新增：容器可启动 | — | **强制（仅 CI）** | CI 构建镜像、起容器、打健康检查 | `.github/workflows/ci.yml` |
| 新增：解析在任意合法输入下确定 | — | **强制** | hypothesis：解析幂等、注释与顺序不变性 | `tests/test_properties.py` |
| 新增：diff 与自身为空、路径对称 | — | **强制** | hypothesis 性质 | `tests/test_properties.py` |
| 新增：混合负载无错误 | — | **强制** | 150 请求 / 15 并发，0 错误、p95 阈值 | `tests/test_load.py` |
| B03 JSON 形状 | — | **部分** | 新增 findings.evidence 数组、快照条数、引用合法性触发器；JSON 字段的完整业务形状仍未全部约束 |
| UI02 证据按钮版本 | — | **强制（已修）** | 缺少 `config_version_id` 时禁用按钮并提示 |
| UI03 解释归属同一 run | — | **强制（已修）** | 解释按最新 run 查询，界面显示 run 标识 |

审查方在临时 PostgreSQL 17.4 上验证了行级触发器、部分索引和 JSON 行为；本机只有 SQLite，因此 PG 的 TRUNCATE 与部署角色权限仍标为未验证。

## 需要修正或收窄的承诺

### C03：未知行"逐条记录"有例外

`line vty`、`frr defaults`、`log` 等显式忽略块内的子命令不会进入 `unparsed`。这是有意设计（这些块与规则无关），但原措辞过宽。

**已改为**：「除少量显式忽略的配置块（如 `line vty`）外，未知行逐条记录」。

### C14：审批人黑名单是启发式

`system / agent / ai / bot / model / assistant` 会被拒绝，但无法穷举所有自动化身份（例如"机器人"、自定义脚本名）。这是产品级判断，不是密码学保证。

**已改为**：「常见系统/Agent 标识会被拒绝；审批人必须由人填写」，不再暗示能证明身份。

### C20 / C21：解释的展示与语义

- 原写「不会进入展示」不准确：列表接口会返回 `rejected` 记录，前端以红色徽标展示，便于审计。
- 原写「只解释已有 Finding」容易被理解为语义校验。实际只校验**引用结构**，不能阻止模型在文字里加入未被引用的背景判断。

**已改为**：「rejected 会保留并带标记展示；校验只覆盖引用结构与行号，不覆盖语义新增」。

### C24：PostgreSQL 未实测

模型与触发器按 PostgreSQL 语法编写，但本机没有实例，`tests` 全部跑在 SQLite。README 与审查指南已标注「PostgreSQL 未在真实实例验证」。

## 仍然存在的结构性限制

| 限制 | 影响 | 是否本轮处理 |
| --- | --- | --- |
| 删除设备会级联删除其审计事件 | 审计"无法抵赖"只对未删除的设备成立 | 否（当前无删除 API，记录为限制） |
| SQLite 触发器可被有库文件权限的人移除 | 本地单用户场景可接受 | 否 |
| 解释层只有 Mock | 真实模型的引用质量未知 | 后续接模型时用同一套评测 |
| 数据库用 `create_all`，无迁移 | 结构变更需重建本地库 | 否（开发阶段） |

## 结论

这轮自查最有价值的发现不是 bug，而是**三类"承诺漂移"**：

1. **范围漂移**：承诺写得比机制覆盖范围大（C03 未知行、C21 语义校验）
2. **层级漂移**：在 HTTP 层承诺，但没在数据库层兑现（C06、C15，上一轮已修）
3. **环境漂移**：在一个环境验证，却按通用能力表述（C24 PostgreSQL）

处理原则：**要么补机制，要么把承诺收窄**。两者都不做，就是在给未来的自己埋雷。
