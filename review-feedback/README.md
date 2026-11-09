# review-feedback

审查意见存放目录。每个 Agent 或每轮审查一个文件：

```
review-feedback/2026-11-09-codex.md
review-feedback/2026-11-10-claude.md
```

格式见仓库根目录 `AGENTS.md` 的「输出要求」。

处理约定：

- 每条意见回复「接受 / 拒绝（附理由）/ 待验证」
- 接受的改动在提交信息里引用编号，例如 `fix(rules): 修复 R002 边界（review #3）`
- 拒绝的意见也保留原文，便于回溯
