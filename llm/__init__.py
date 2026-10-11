"""LLM layer: every model call in the platform goes through here.

- ``llm.registry``  versioned prompt files (prompts/<id>/vN.md)
- ``llm.client``    LLMClient (anthropic | fake), retries, JSON parsing, caching, budget guard
- ``llm.tracing``   JSONL (+ optional SQLite) trace of every call; ``python -m llm.tracing summary``
- ``llm.cost``      pricing table, cost_usd(), ``python -m llm.cost estimate``
- ``llm.features``  need_classifier, account_brief, outreach
"""

# Submodules are imported explicitly (``from llm.client import LLMClient``) so that
# ``python -m llm.cost`` / ``python -m llm.tracing`` run without double-import warnings.
