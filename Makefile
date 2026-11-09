PY ?= python3

.PHONY: test run eval cases clean

test:
	$(PY) -m pytest

run:
	$(PY) -m uvicorn api.main:app --reload --port 8000

cases:
	$(PY) evals/build_cases.py

eval:
	$(PY) evals/run_eval.py

clean:
	rm -rf .pytest_cache
