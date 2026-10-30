.PHONY: install lint test synth diff deploy seed verify verify-live demo teardown research research-synthetic
export PYTHONPATH := services:$(PYTHONPATH)
PYTHON ?= .venv/bin/python
CDK_OUTDIR ?= cdk.out
install:
	python3.12 -m pip install -e '.[dev]'
lint:
	ruff check services infra tests scripts research
test:
	$(PYTHON) -m pytest -q
synth:
	CDK_OUTDIR=$(CDK_OUTDIR) JSII_RUNTIME_PACKAGE_CACHE_ROOT=.cache/jsii $(PYTHON) infra/app.py
diff:
	@echo 'Review docs/aws-setup.md and the synthesized template before any AWS change.'
	cdk diff
deploy:
	@echo 'Deployment requires explicit user approval after reviewing the diff.'
	cdk deploy --all
seed:
	python3 scripts/seed.py
verify:
	python3 scripts/verify_local.py
verify-live:
	@echo 'Live verifier makes no AWS calls without --confirm-account and --preflight or --apply.'
	$(PYTHON) scripts/verify_live.py
demo:
	$(PYTHON) scripts/demo.py
teardown:
	@test -n "$(AWS_EXPECTED_ACCOUNT_ID)" || { echo 'Set AWS_EXPECTED_ACCOUNT_ID after confirming the intended account.'; exit 2; }
	$(PYTHON) scripts/teardown.py --expected-account "$(AWS_EXPECTED_ACCOUNT_ID)" --dry-run
research:
	$(PYTHON) -m research.replay.run --protocol research/protocol.md --manifest research/data_manifest.md

research-synthetic:
	python3 research/run.py --config research/config.json
