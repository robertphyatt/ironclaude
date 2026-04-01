.PHONY: test run install

install:
	pip install -e ".[dev]"

test:
	python -m pytest tests/ -v

run:
	@set -a && . ./.env && set +a && python -m ironclaude.main
