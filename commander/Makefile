.PHONY: test run install stop follow-run

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

stop:
	@PID_FILE=/tmp/ic-daemon.pid; \
	if [ ! -f "$$PID_FILE" ]; then \
		echo "No PID file at $$PID_FILE -- daemon is not running."; \
		exit 0; \
	fi; \
	PID=$$(cat "$$PID_FILE"); \
	if ! kill -0 "$$PID" 2>/dev/null; then \
		echo "PID $$PID not running (stale PID file)."; \
		exit 0; \
	fi; \
	kill -TERM "$$PID" && echo "Sent SIGTERM to PID $$PID." || { echo "Failed to send SIGTERM to PID $$PID."; exit 1; }; \
	sleep 2; \
	if kill -0 "$$PID" 2>/dev/null; then \
		echo "Daemon (PID $$PID) still running after SIGTERM."; \
		exit 1; \
	else \
		echo "Daemon (PID $$PID) stopped."; \
	fi

follow-run:
	tail -f /tmp/ironclaude-daemon.log
