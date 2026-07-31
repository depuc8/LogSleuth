PYTHON ?= python3

.PHONY: test unit shell demo samples examples clean

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

# Regenerate the example reports from the samples.
examples:
	bin/logsleuth analyze -q --year 2026 samples/db-pool/syslog.log samples/db-pool/app.log -o examples/db-pool.rca.md
	bin/logsleuth analyze -q --year 2026 samples/oom/syslog.log samples/oom/app.log -o examples/oom.rca.md

clean:
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf out
