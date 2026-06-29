# QRESPONDER developer tasks. (Linux/macOS; Windows users: run the commands
# directly or use Git Bash.)

.PHONY: install install-retrieval test eval eval-baseline demo lint

install:
	pip install -e ".[anthropic,openai,dev]"

install-retrieval:
	pip install -e ".[anthropic,openai,retrieval,dev]"

test:
	pytest

eval:
	LLM_PROVIDER=mock qresponder eval --set eval.yaml --kb tests/fixtures/kb --qa qa.example.yaml

# Deterministic, reproducible baseline (MockProvider) — the published numbers.
eval-baseline:
	LLM_PROVIDER=mock qresponder eval --set eval.yaml --kb tests/fixtures/kb --qa qa.example.yaml

demo:
	bash scripts/demo.sh
