PY ?= python3

.PHONY: test run clean

test:
	$(PY) -m pytest

run:
	$(PY) -m uvicorn api.main:app --reload --port 8000

clean:
	rm -rf .pytest_cache
