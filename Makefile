.PHONY: help install web ask test check answers sync clean

help:
	@echo "install   create .venv and install (incl. dev + sdk extras)"
	@echo "web       run the web UI on :8080"
	@echo "test      run the test suite"
	@echo "check     tests + prompt-sync check"
	@echo "answers   regenerate docs/EXPECTED_ANSWERS.md from live tool calls"
	@echo "sync      regenerate the Claude Code agent/command from agent/prompt.py"
	@echo "ask Q=... ask one question from the CLI"

install:
	python3 -m venv .venv
	.venv/bin/pip install -q -e ".[dev,sdk]"
	@.venv/bin/python -m agent.runner --list-backends

web:
	.venv/bin/python -m web.app

ask:
	@.venv/bin/python -m agent.runner "$(Q)"

test:
	.venv/bin/python -m pytest tests/ -q

check: test
	.venv/bin/python scripts/sync_prompt.py --check

answers:
	.venv/bin/python scripts/reference_answers.py

sync:
	.venv/bin/python scripts/sync_prompt.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache *.egg-info
