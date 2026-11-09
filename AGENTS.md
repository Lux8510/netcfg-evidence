# AGENTS.md

本文件给在仓库里运行的编码/审查 Agent（如 Codex）看。默认任务：**只审查，不改源码**。

## 项目一句话

把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。
当前是 W1：解析器、漂移检测、持久化 API、4 条确定性规则、20 例评测。

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
7. `tests/` — 是否只有 happy path

## 必须质疑的五点

1. `unparsed` 里的行，会不会让本该违规的规则被判成 satisfied？
2. canonical 哈希排序后，是否存在"顺序有意义"的语法被错误归一化？
3. 同一份配置重复导入是否真的幂等？并发下呢？
4. 基线替换时，是否可能出现两条 active 记录？
5. 20 个评测案例的期望值，是否真由人工写死，而不是由实现生成？

## 已知限制（不要重复报）

- 只支持 FRR 语法子集，Cisco IOS 未实现
- 没有前端、变更单、审批流、LLM 解释层
- 评测只覆盖 20 个案例，不代表真实世界准确率
- 并发测试尚未覆盖

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

## 不要做的事

- 不要 `git commit`、不要 `git push`
- 不要改动 `samples/`、`evals/cases.json`（那是评测基准）
- 不要为了让测试通过而修改测试断言
- 不要引入新依赖
