# 整体自查（2026-11-10）

交付前与每轮审查后，把所有可自动化的检查跑一遍，并核对文档与代码是否一致。最近更新：2026-11-10（W5 之后）。

## 自动化检查

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 单元/接口 | `pytest` | 201 passed |
| 浏览器 E2E | `pytest -m e2e tests/test_e2e_ui.py` | 15 passed |
| 真实并发 | `pytest -m concurrency tests/test_concurrency.py` | 3 passed |
| 短压测 | `pytest -m load tests/test_load.py` | 1 passed |
| 漂移评测 | `evals/run_eval.py` | 20/20，P/R 1.0000 |
| 规则评测 | `evals/run_rules_eval.py` | 15/15，60 个判定 |
| 解释校验 | `evals/run_explain_eval.py` | 17/17 |
| SSH 采集 | `pytest tests/test_ssh_collection.py` | 17 passed（含假 SSH 设备） |
| 发布检查 | `tools/check_publication.py` | PASS（126 文件） |
| 迁移一致性 | `alembic upgrade head && alembic check` | No new upgrade operations（7 个迁移） |
| 前端产物一致性 | `npm run build` 后 `git status -- web` | 无差异 |
| CI | GitHub Actions | success（含容器 healthy） |

## 文档与代码一致性

| 项 | 问题 | 处理 |
| --- | --- | --- |
| README 启动说明 | 仍写"前端跳转 `/login.html`"，新前端是单页应用 | 改为"显示登录视图" |
| README 目录 | 列出已移除的 `docs/INTERVIEW_NOTES.md` | 替换为 `frontend/`、`web/`、`docs/model-comparison.md` |
| README 评测数 | 解释校验仍写 16 例 | 改为 17 例 |
| README 截图 | 拓扑图重复出现两次 | 去掉重复，补登录页 |
| 审查指南 | 仍指向旧 `web/app.js` | 改为 `frontend/src/` |
| 承诺表 | C22 证据路径过时 | 改为 `Dashboard.tsx:loadDeviceData` |
| README 测试数 | 未包含 SSH/主题新增用例 | 更新为 201 / 15 |
| 环境变量示例 | 缺少 SSH 白名单与严格校验 | 补 `NETCFG_SSH_ALLOWED_*`、`STRICT_HOST_KEY` |

## 仓库卫生

- 无 `.env`、`.db`、`node_modules`、`evals/model-runs` 被跟踪
- `.gitignore` 覆盖 SQLite WAL/SHM、Hypothesis 缓存、构建产物目录
- 无 TODO/FIXME 残留
- `make` 目标齐全：`ui / test / e2e / prop / concurrency / load / compare / model-check / user / run / migrate / revision / cases / eval / bundle / check / review-kit / clean`

## 仍然存在的边界（不修，写清楚）

| 边界 | 说明 |
| --- | --- |
| PostgreSQL 只做过审查方的临时实例验证 | 本机没有实例；TRUNCATE 与部署角色权限未在本机验证 |
| SSH 只对假设备验证 | 未接真机；分页、提示符、厂商语法未验证 |
| 默认严格校验主机指纹 | 首次接入需登记指纹，或用环境变量显式关闭 |
| Docker 仅 CI 验证 | 本机没有容器运行时 |
| 登录限流是进程内计数 | 重启清零，多进程部署需外部限流 |
| CSRF 依赖 SameSite + Origin 校验 | 没有显式 token；非浏览器客户端不受 Origin 约束 |
| 3D 无帧率测量 | 只按宽度与减少动效偏好决定是否加载 |
| 模型对比是 15 例单次 | 无统计显著性，无语义评分 |
