"""Prompt registry (contract §4).

Prompts live in ``prompts/<prompt_id>/<version>.md``::

    ---
    id: need-classifier
    version: v2
    model: claude-haiku-5-5
    max_tokens: 400
    temperature: 0
    output: json
    changelog: "..."
    ---
    <!-- system -->
    ...
    <!-- user -->
    ... {{company_json}} ...

``sha256`` is the hash of the whole file, so any edit (even whitespace) is
visible in traces and eval results. Prompts are code: change them via a new
version file, never by editing a version that has been evaluated.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROMPTS_DIR = Path(os.environ.get("PROMPTS_DIR", Path(__file__).resolve().parent.parent / "prompts"))

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.S)
_SYSTEM_MARK = "<!-- system -->"
_USER_MARK = "<!-- user -->"
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_VERSION_RE = re.compile(r"^v(\d+)$")

REQUIRED_META = ("id", "version", "model", "max_tokens", "output")


class PromptError(ValueError):
    """Malformed prompt file or bad render call."""


class MissingVariableError(PromptError, KeyError):
    """A ``{{var}}`` in the template had no value supplied."""


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    model: str
    max_tokens: int
    temperature: float
    output: str  # "json" | "text"
    system: str
    user_template: str
    sha256: str
    meta: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)
    path: str | None = field(default=None, compare=False, repr=False)

    @property
    def variables(self) -> list[str]:
        seen: list[str] = []
        for name in _VAR_RE.findall(self.system + self.user_template):
            if name not in seen:
                seen.append(name)
        return seen

    @property
    def required_keys(self) -> list[str]:
        """Keys the parsed JSON output must contain (front-matter ``required_keys``)."""
        return list(self.meta.get("required_keys") or [])

    def render(self, **variables: Any) -> str:
        """Render the user template. Raises MissingVariableError on any unfilled ``{{var}}``."""
        return _render(self.user_template, variables, self.id, self.version)

    def render_system(self, **variables: Any) -> str:
        return _render(self.system, variables, self.id, self.version)


def _render(template: str, variables: dict[str, Any], pid: str, ver: str) -> str:
    missing = [n for n in _VAR_RE.findall(template) if n not in variables or variables[n] is None]
    if missing:
        raise MissingVariableError(
            f"prompt {pid}@{ver}: missing template variable(s): {sorted(set(missing))}"
        )
    return _VAR_RE.sub(lambda m: str(variables[m.group(1)]), template)


def parse_prompt_text(text: str, path: str | None = None) -> Prompt:
    m = _FRONT_MATTER_RE.match(text.replace("\r\n", "\n"))
    if not m:
        raise PromptError(f"{path or 'prompt'}: missing YAML front-matter block")
    meta = yaml.safe_load(m.group(1)) or {}
    if not isinstance(meta, dict):
        raise PromptError(f"{path or 'prompt'}: front-matter must be a mapping")
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise PromptError(f"{path or 'prompt'}: front-matter missing keys {missing}")
    if meta["output"] not in ("json", "text"):
        raise PromptError(f"{path or 'prompt'}: output must be 'json' or 'text'")
    body = m.group(2)
    si, ui = body.find(_SYSTEM_MARK), body.find(_USER_MARK)
    if si == -1 or ui == -1 or ui < si:
        raise PromptError(f"{path or 'prompt'}: need '{_SYSTEM_MARK}' then '{_USER_MARK}' sections")
    system = body[si + len(_SYSTEM_MARK):ui].strip()
    user = body[ui + len(_USER_MARK):].strip()
    return Prompt(
        id=str(meta["id"]),
        version=str(meta["version"]),
        model=str(meta["model"]),
        max_tokens=int(meta["max_tokens"]),
        temperature=float(meta.get("temperature", 0) or 0),
        output=str(meta["output"]),
        system=system,
        user_template=user,
        sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        meta=meta,
        path=path,
    )


def list_versions(prompt_id: str, prompts_dir: str | Path | None = None) -> list[str]:
    """All versions for a prompt id, sorted numerically (v1, v2, ..., v10)."""
    d = Path(prompts_dir or PROMPTS_DIR) / prompt_id
    if not d.is_dir():
        return []
    versions = [p.stem for p in d.glob("v*.md") if _VERSION_RE.match(p.stem)]
    return sorted(versions, key=lambda v: int(v[1:]))


def load_prompt(prompt_id: str, version: str | None = None, prompts_dir: str | Path | None = None) -> Prompt:
    """Load ``prompts/<prompt_id>/<version>.md``; ``version=None`` = highest vN."""
    base = Path(prompts_dir or PROMPTS_DIR)
    if version is None:
        versions = list_versions(prompt_id, base)
        if not versions:
            raise FileNotFoundError(f"no prompt versions found for '{prompt_id}' in {base}")
        version = versions[-1]
    path = base / prompt_id / f"{version}.md"
    if not path.is_file():
        raise FileNotFoundError(f"prompt not found: {path} (available: {list_versions(prompt_id, base)})")
    prompt = parse_prompt_text(path.read_text(encoding="utf-8"), str(path))
    if prompt.id != prompt_id or prompt.version != version:
        raise PromptError(
            f"{path}: front-matter id/version ({prompt.id}@{prompt.version}) "
            f"does not match path ({prompt_id}@{version})"
        )
    return prompt
