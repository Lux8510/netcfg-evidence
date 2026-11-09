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
| C26 | 解释层可替换为真实模型 | README | `Explainer` 协议 + OpenAI 兼容适配器 + 环境变量 | **强制**（适配器未对真实 API 验证） | `tests/test_providers.py` |
| C27 | 未配置密钥时不会静默退回 Mock | README | `build_explainer` 抛 409 | **强制** | `tests/test_providers.py::test_build_explainer_defaults_to_mock_and_refuses_missing_key` |
| C28 | 存在数据库里的旧库也会补上追加式触发器 | README（隐含） | 启动时 `ensure_append_only` 幂等执行 | **强制** | `tests/test_bootstrap.py` |
| C25 | 评测期望值独立于实现 | README 评测节 | 期望值在生成脚本中手写，运行时不调用被测实现 | **强制**（代码层面） | `evals/build_*.py`、`tests/test_eval.py` |

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
