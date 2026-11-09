PY ?= python3

.PHONY: test clean

test:
	$(PY) -m pytest

clean:
	rm -rf .pytest_cache
