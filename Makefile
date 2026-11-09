PY ?= python3

.PHONY: test run eval cases bundle check review-kit clean

test:
	$(PY) -m pytest

run:
	$(PY) -m uvicorn api.main:app --reload --port 8000

cases:
	$(PY) evals/build_cases.py

eval:
	$(PY) evals/run_eval.py

bundle:
	$(PY) tools/build_review_bundle.py

check:
	$(PY) tools/check_publication.py

review-kit:
	@echo "1) git clone 或解压审查包到本地"
	@echo "2) 在仓库根目录运行: codex"
	@echo "3) 输入: 按 AGENTS.md 审查本仓库，只写 review-feedback/"
	@echo "4) 提交 review-feedback/*.md 并推送，或在对话里贴回"

clean:
	rm -rf .pytest_cache
