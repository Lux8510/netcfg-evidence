PY ?= python3

.PHONY: test run eval cases bundle check clean

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

clean:
	rm -rf .pytest_cache
