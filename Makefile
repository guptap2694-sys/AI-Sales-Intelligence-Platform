PYTHON ?= python3
INPUT  ?= data/sample.parquet
OUT    ?= app/data/accounts.json
TOP    ?= 5000
LLM_TOP ?= 0

.PHONY: install test profile build build-synthetic eval bundle all

install:
	$(PYTHON) -m pip install -r requirements.txt

test:
	$(PYTHON) -m pytest -q

# Run this first on any new data file: columns, types, null rates, top values, detected mapping.
profile:
	$(PYTHON) -m pipeline.ingest profile --input $(INPUT)

build:
	$(PYTHON) -m pipeline.build --input $(INPUT) --out $(OUT) --top $(TOP) --llm-top $(LLM_TOP)

# Synthetic fixture -> app/data/accounts.sample.json (meta.synthetic = true)
build-synthetic:
	$(PYTHON) -m pipeline.build --input tests/fixtures/synthetic_companies.csv \
		--out app/data/accounts.sample.json --synthetic

eval:
	$(PYTHON) -m evals.run_evals

bundle:
	$(PYTHON) scripts/bundle_app.py

all: test build eval bundle
