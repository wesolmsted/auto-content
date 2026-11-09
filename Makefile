.PHONY: check fix

check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy --strict engine
	uv run pytest

fix:
	uv run ruff format .
	uv run ruff check --fix .
