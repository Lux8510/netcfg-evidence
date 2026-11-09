"""Build a self-contained review bundle: sources + manifest + review request."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_files import ROOT, files  # noqa: E402

OUT = ROOT / "review-bundles"
PREFIX = "netcfg-evidence"

REVIEW_REQUEST = """# 审查请求

请只基于压缩包内的文件进行审查，不要假设包外存在其他代码或数据。
具体的审查协议、读码顺序和输出格式见包内 `AGENTS.md`。

## 项目一句话

把网络设备配置当作有版本的证据：任何一条判定都能指回具体的配置行、规则来源和审批记录。

## 当前范围（W1）

- 确定性 FRR 配置解析器（声明过的语法子集，未知行记录不丢弃）
- 结构化漂移检测（每个差异带两侧行号）
- SQLite/PostgreSQL 持久化 + 10 个 HTTP 接口
- 4 条确定性规则 + 三值判定（violated / satisfied / undetermined）
- 20 个人工标注的漂移评测案例

## 明确不在范围内

- 不连接真实设备、不下发配置、不做全网转发仿真
- 前端、变更单、审批流、LLM 解释层尚未实现
- 只支持 FRR 语法子集，不支持 Cisco IOS

## 希望你怎么审

1. 正确性：解析与漂移逻辑是否有漏判/误判，边界是否覆盖
2. 设计：数据模型与接口是否合理，是否有更简单的做法
3. 风险：哪里会在真实配置上出错
4. 可测试性：现有 48 项测试缺了什么关键用例

## 输出格式（便于逐条处理）

对每条意见给出：
- 结论：接受 / 拒绝 / 待验证
- 位置：文件 + 函数
- 理由：为什么这是问题
- 建议：最小修改方案

不接受"建议加抽象""建议换框架"这类没有具体理由的意见；请说明它解决什么具体问题。

## 自检命令

```bash
python3 -m pytest
python3 evals/run_eval.py
python3 tools/check_publication.py
```
"""


def short_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.strip()
    except Exception:
        return "nogit"


def main() -> int:
    selected = files()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT.mkdir(exist_ok=True)
    target = OUT / f"{PREFIX}-review-{short_sha()}-{stamp}.zip"
    manifest = {"schema_version": 1, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "git_commit": short_sha(), "file_count": len(selected), "files": []}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in selected:
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr(f"{PREFIX}/{relative}", data)
            manifest["files"].append({"path": relative, "bytes": len(data),
                                      "sha256": hashlib.sha256(data).hexdigest()})
        archive.writestr(f"{PREFIX}/MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        archive.writestr(f"{PREFIX}/REVIEW_REQUEST.md", REVIEW_REQUEST)
    print(f"Bundle: {target}")
    print(f"Files:  {len(selected)}")
    print(f"SHA256: {hashlib.sha256(target.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
