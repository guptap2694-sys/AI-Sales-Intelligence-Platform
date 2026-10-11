#!/usr/bin/env python3
"""Bundle the static app into one self-contained HTML file.

Inlines app/styles.css, app/app.js and an accounts JSON into dist/index.html so the
app can be hosted anywhere (GitHub Pages, S3, a claude.ai Artifact) with no fetches.

    python scripts/bundle_app.py                       # app/data/accounts.json, else the sample
    python scripts/bundle_app.py --data path/to.json   # any contract §3 file
    python scripts/bundle_app.py --max-accounts 3000   # keep the top-N by priority_score

Outputs:
    dist/index.html     full HTML document (static hosting)
    dist/artifact.html  same page without <!doctype>/<html>/<head>/<body> wrappers, for
                        hosts that supply their own skeleton (claude.ai Artifacts)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
LIMIT_BYTES = 16 * 1024 * 1024


def pick_data(arg: str | None) -> Path:
    if arg:
        p = Path(arg)
        if not p.is_absolute():
            p = (Path.cwd() / p).resolve()
        if not p.exists():
            sys.exit(f"error: data file not found: {p}")
        return p
    for cand in (APP / "data" / "accounts.json", APP / "data" / "accounts.sample.json"):
        if cand.exists():
            return cand
    sys.exit("error: no app/data/accounts.json or accounts.sample.json found")


def load_doc(path: Path, max_accounts: int | None) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    accounts = doc.get("accounts") or []
    if max_accounts is not None and len(accounts) > max_accounts:
        accounts = sorted(accounts, key=lambda a: -(a.get("priority_score") or 0))[:max_accounts]
        doc["accounts"] = accounts
        meta = doc.setdefault("meta", {})
        meta["rows_included"] = len(accounts)
        meta["bundle_trimmed_to"] = max_accounts
    return doc


def build(data_path: Path, max_accounts: int | None) -> tuple[str, str, int]:
    html = (APP / "index.html").read_text(encoding="utf-8")
    css = (APP / "styles.css").read_text(encoding="utf-8")
    js = (APP / "app.js").read_text(encoding="utf-8")
    doc = load_doc(data_path, max_accounts)
    payload = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    # keep the JSON from closing its <script> early; "<\/" is a valid JSON escape
    payload = payload.replace("</", "<\\/").replace("<!--", "<\\!--")
    js_safe = js.replace("</script", "<\\/script")

    try:
        rel = data_path.relative_to(ROOT).as_posix()
    except ValueError:
        rel = data_path.name

    css_tag = '<link rel="stylesheet" href="styles.css">'
    js_tag = '<script type="module" src="app.js"></script>'
    if css_tag not in html or js_tag not in html:
        sys.exit("error: app/index.html no longer contains the expected styles.css / app.js tags")
    data_tag = f'<script type="application/json" id="embedded-data" data-source="{rel}">{payload}</script>'
    html = html.replace(css_tag, f"<style>\n{css}\n</style>")
    html = html.replace(js_tag, f"{data_tag}\n<script type=\"module\">\n{js_safe}\n</script>")

    # fragment for skeleton-supplying hosts: <title> + head links/styles first, then body content
    head = re.search(r"<head>(.*?)</head>", html, re.S).group(1)
    body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
    head = re.sub(r"<meta[^>]*>\s*", "", head)
    fragment = head.strip() + "\n" + body.strip() + "\n"
    return html, fragment, len(doc.get("accounts") or [])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", help="accounts JSON (default: app/data/accounts.json, else the sample)")
    ap.add_argument("--out", default=str(ROOT / "dist" / "index.html"), help="output HTML path")
    ap.add_argument("--max-accounts", type=int, default=None, help="keep only the top-N accounts by priority_score")
    args = ap.parse_args()

    data_path = pick_data(args.data)
    html, fragment, n = build(data_path, args.max_accounts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    frag_out = out.with_name("artifact.html")
    frag_out.write_text(fragment, encoding="utf-8")

    size = out.stat().st_size
    print(f"bundled {n} accounts from {data_path} -> {out} ({size / 1024 / 1024:.2f} MB)")
    print(f"artifact fragment -> {frag_out}")
    if size > LIMIT_BYTES:
        per = size / max(n, 1)
        suggest = int(LIMIT_BYTES * 0.95 / per)
        print(f"WARNING: output is over 16 MB. Re-run with --max-accounts {suggest} (about {per / 1024:.1f} KB per account).", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
