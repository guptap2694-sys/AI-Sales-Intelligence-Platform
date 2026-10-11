# Skills

Skills are versioned, self-contained playbooks that tell any agent (Claude Code, Cursor,
an API-driven agent, or a human) **when** to do a task, **what** goes in and out, **which
prompts** (by path and version) it depends on, **what is rules vs LLM**, and **how to run it**.

| skill | version | what it does | prompts |
|---|---|---|---|
| [`account-scoring`](account-scoring/SKILL.md) | 1.0.0 | Rules score every company; Haiku need-classifier judges pre-filtered accounts | `need-classifier` v2 (v1 baseline) |
| [`outreach-draft`](outreach-draft/SKILL.md) | 1.0.0 | Sonnet drafts a ≤120-word, signal-grounded email + 3–4 bullet brief | `outreach-draft` v1, `account-brief` v1 |

## Format
Each `skills/<name>/SKILL.md` starts with YAML front-matter:

```yaml
name: account-scoring          # stable id, matches the directory
description: ...               # one paragraph: what + when (agents match on this)
version: 1.0.0                 # semver; bump minor for new steps, major for I/O changes
triggers: [...]                # example requests that should load the skill
```
followed by: When to trigger · Inputs · Outputs · Steps · Dependent prompts · Rules vs LLM split · Guardrails · How to run (CLI / Python / raw API) · Worked example (illustrative).

## How agents load them
- **Claude Code**: Agent Skills are discovered from `.claude/skills/<name>/SKILL.md`. Symlink or copy:
  `mkdir -p .claude/skills && ln -s ../../skills/account-scoring .claude/skills/account-scoring`
  (same for `outreach-draft`). Claude reads the front-matter `name`/`description` to decide when to load the full file, then follows its steps and runs the listed `python -m` commands.
- **Cursor / other IDE agents**: reference the file in a rule or prompt (e.g. `@skills/outreach-draft/SKILL.md`), or add `skills/*/SKILL.md` to the project rules so the agent reads it before acting.
- **API agents (Claude API / Agent SDK / any LLM)**: put the SKILL.md body in the system prompt (or upload it as an Agent Skill) and expose the CLI commands as tools; or follow the "Raw API" section to call the model directly with the versioned prompt file.
- **Humans**: each SKILL.md is also the runbook.

## Versioning rules
- Prompts are versioned independently (`prompts/<id>/vN.md`, sha256 recorded in every trace). A skill pins the prompt versions it was validated with; changing a dependency means bumping the skill version.
- Never edit a prompt version that has eval results; add `vN+1` with a `changelog` and re-run `python -m evals.run_evals`.
- Worked examples in skills are illustrative and hand-written; real outputs live in traces and eval results.
