.PHONY: config-check test

config-check:
	PYTHONPATH=src python -m texperiment.cli config-check

test:
	PYTHONPATH=src pytest -q
