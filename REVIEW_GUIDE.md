# 审查指南

这份指南给"审查方"看：一个不熟悉项目的人（或另一个 AI），怎样在 20 分钟内判断这份代码值不值得信任。

## 1. 先跑三条命令

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest            # 48 项
.venv/bin/python evals/run_eval.py    # 20 个漂移案例
.venv/bin/python tools/check_publication.py
```

三条都通过，才进入读代码环节。跑不过就是结论，不用继续。

## 2. 按这个顺序读（约 15 分钟）

| 顺序 | 文件 | 看什么 |
| --- | --- | --- |
| 1 | `DESIGN.md` | 边界声明是否清楚，有没有"什么不做" |
| 2 | `domain/netcfg/parser.py` | 未知行是否记录、行号是否保留、是否有静默丢弃 |
| 3 | `domain/netcfg/drift.py` | 差异是否有误报（尤其"无变化"和"仅注释变化"） |
| 4 | `domain/netcfg/rules.py` | 三值判定是否真的成立，`undetermined` 会不会被当成通过 |
| 5 | `domain/netcfg/service.py` | 写操作的唯一入口、幂等、版本不可变 |
| 6 | `engine/models.py` | 约束是否在数据库层（唯一键、部分索引） |
| 7 | `domain/netcfg/change_requests.py` | 状态机能否绕过、审批人能否伪造、审计是否只追加 |
| 8 | `domain/netcfg/explain.py` | 引用校验的边界、rejected 是否影响结论 |
| 9 | `frontend/src/` | XSS、异步写回、焦点与键盘可用性 |
| 10 | `tests/` | 是否只测了"能跑通"，有没有边界与反例 |

## 3. 重点质疑这五处

1. `unparsed` 里的行，是否可能导致某条规则本该违规却被判为 satisfied？
2. canonical 哈希排序后，是否存在"顺序有意义"的语法被错误归一化？
3. 同一份配置重复导入，是否真的返回同一个版本？（并发下呢？）
4. 基线替换时，是否可能出现两条 active 记录？
5. 20 个评测案例的期望值，是否真的由人工写死，而不是由实现生成？
6. 变更单能否跳过 approve 直接 applied？审批人能否是 system/agent？
7. 解释校验能否被绕过（正文行号与引用不一致、混入非证据行）？
8. 前端有没有未转义的用户内容（XSS）？
9. 页面加载是否会产生写操作（GET 有副作用）？

## 4. 本轮只看四件事

1. 认证与会话安全：CSRF、会话固定、角色绕过、Cookie 属性、限流缺失
2. 新前端（React）的 XSS 与设备切换时的状态隔离
3. 3D/动效的性能开关与可访问性
4. `docs/CLAIMS_AUDIT.md` 新增条目是否真被机制强制（构建产物一致、3D 开关等）

其余问题仍可提，但优先级低于这四项。

## 5. 已知限制（不需要再报）

- 只支持 FRR 语法子集，Cisco IOS 未实现
- 解释层只有 Mock，未接真实模型
- 没有登录与权限体系（本地单用户）
- 评测只覆盖 20 个案例，不代表真实世界准确率
- 并发只做了竞争回退的单元级覆盖，未做压力测试

## 6. 反馈格式

对每条意见写：**结论（接受 / 拒绝 / 待验证）+ 文件与函数 + 理由 + 最小修改方案**。

没有具体理由的"建议加抽象""建议换框架"会被拒绝，并记录拒绝原因。
