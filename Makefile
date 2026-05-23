.PHONY: install dev test lint typecheck format check api clean

install:
	pip install -e .

dev:
	pip install -e ".[dev]"

test:
	pytest

test-fast:
	pytest -x -q --no-cov

lint:
	ruff check src tests
	black --check src tests

format:
	ruff check --fix src tests
	black src tests

typecheck:
	mypy src/repoheal

check: lint typecheck test

api:
	uvicorn repoheal.api.main:app --reload --host 0.0.0.0 --port 8000

clean:
	rm -rf build dist *.egg-info .mypy_cache .ruff_cache .pytest_cache .coverage coverage.xml htmlcov
	find . -type d -name __pycache__ -exec rm -rf {} +
