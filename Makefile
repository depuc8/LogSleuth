PYTHON ?= python3

.PHONY: test unit shell demo samples clean

test: unit shell

unit:
	$(PYTHON) -m unittest discover -s tests -t . -v

shell:
	tests/run_shell_tests.sh

# Analyze the bundled sample incidents.
demo:
	bin/logsleuth analyze --year 2026 samples/db-pool/syslog.log samples/db-pool/app.log

# Regenerate samples/ (output is deterministic).
samples:
	$(PYTHON) tools/gen_samples.py

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf out
