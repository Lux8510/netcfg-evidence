PY ?= python3

.PHONY: ui test prop e2e concurrency load model-check user run migrate revision eval cases bundle check review-kit clean

ui:
	cd frontend && npm ci --no-audit --no-fund && npm run build

test:
	$(PY) -m pytest

e2e:
	$(PY) -m pytest -m e2e tests/test_e2e_ui.py

prop:
	$(PY) -m pytest tests/test_properties.py

concurrency:
	$(PY) -m pytest -m concurrency tests/test_concurrency.py

load:
	$(PY) -m pytest -m load tests/test_load.py

compare:
	$(PY) scripts/compare_models.py

model-check:
	$(PY) scripts/check_model.py

user:
	@echo "用法: make user USER=何深 ROLE=editor（密码读 NETCFG_USER_PASSWORD 或交互输入）"
	$(PY) scripts/create_user.py --username "$(USER)" --role "$(ROLE)"

run:
	$(PY) -m uvicorn api.main:app --reload --port 8000

migrate:
	$(PY) -m alembic upgrade head

revision:
	$(PY) -m alembic revision --autogenerate -m "$(m)"

cases:
	$(PY) evals/build_cases.py
	$(PY) evals/build_rule_cases.py
	$(PY) evals/build_explain_cases.py

eval:
	$(PY) evals/run_eval.py
	$(PY) evals/run_rules_eval.py
	$(PY) evals/run_explain_eval.py

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
