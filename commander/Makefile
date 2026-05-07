.PHONY: test run install

VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip

install:
	python3 -m venv $(VENV)
	$(PIP) install -e ".[dev]"
	$(PIP) freeze > requirements.txt

test:
	$(PYTHON) -m pytest tests/ -v

run:
	@$(PIP) install -qe . && set -a && . ./.env && set +a && $(PYTHON) -u -m ironclaude.main 2>&1 | tee -a /tmp/ironclaude-daemon.log
