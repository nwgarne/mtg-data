#!/usr/bin/env python3
"""Mirror the Magic Comprehensive Rules into rules/ as cr-raw.txt + cr.json + manifest.json.

Discovers the current CR .txt link on https://magic.wizards.com/en/rules,
downloads it only when the effective date changed, parses it with the same
semantics as the podsensei parse-cr.js reference parser, validates the parse,
and writes the three artifacts. No change means no writes, so the workflow's
commit step stays a no-op on quiet days.

Standard library only. Designed for GitHub Actions ubuntu-latest under a
10-minute timeout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RULES_DIR = REPO_ROOT / "rules"
RAW_PATH = RULES_DIR / "cr-raw.txt"
JSON_PATH = RULES_DIR / "cr.json"
MANIFEST_PATH = RULES_DIR / "manifest.json"

RULES_PAGE = "https://magic.wizards.com/en/rules"
USER_AGENT = "nwgarne-mtg-data/1.0 (https://github.com/nwgarne/mtg-data)"

MIN_TXT_BYTES = 500_000
MIN_RULES = 3000
MIN_GLOSSARY = 600
SHRINK_FLOOR = 0.95
# 205.4c and 509.1b guard the U+2028 normalization: their head lines carry a
# mid-line LINE SEPARATOR in the official text and vanish if it is not folded.
REQUIRED_RULES = ("100.1", "601.2a", "704.5", "903.4", "205.4c", "509.1b")

# The .txt href on the rules page. The filename historically contains a
# literal space ("MagicCompRules 20260807.txt"); some page revisions encode
# it as %20 instead, so the character class allows both forms.
TXT_URL_RE = re.compile(
    r"https://media\.wizards\.com/[^\"'<>]*?MagicCompRules[^\"'<>]*?\.txt",
    re.IGNORECASE,
)

# A rule-number token at line start, e.g. "601.2." or "601.2a" or "100.1b".
# Top-level section headers like "100. General" are intentionally NOT matched.
# This is parse-cr.js's /^(\d{3}\.\d+[a-z]?)(?:\.)?\s+(.*)$/ with ASCII digit
# classes. The official text carries U+2028 LINE SEPARATOR inside a few rule
# head lines (205.4c, 509.1b in the Aug 2026 CR); parse_cr folds U+2028 and
# U+2029 to spaces before matching so those rules are kept. parse-cr.js in
# podsensei-cr-staging applies the same fold to stay in sync.
RULE_HEAD = re.compile(
    r"^([0-9]{3}\.[0-9]+[a-z]?)(?:\.)?\s+([^\n\r\u2028\u2029]*)\Z"
)


def http_get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def emit_github_output(key: str, value: str) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def clean(s: str) -> str:
    """Collapse whitespace runs to single spaces and trim (parse-cr.js clean)."""
    return re.sub(r"\s+", " ", s).strip()


def index_of_line(lines: list[str], target: str, start_at: int = 0) -> int:
    for i in range(start_at, len(lines)):
        if lines[i].strip() == target:
            return i
    return -1


def parse_cr(text: str) -> dict:
    """Port of parse-cr.js. Input must already be BOM-stripped and LF-normalized."""
    # Fold Unicode line/paragraph separators to spaces. They appear mid-line in
    # a handful of rule heads; left alone they make those rules unparseable and
    # the rules are silently dropped. cr-raw.txt keeps the original characters.
    text = text.replace(" ", " ").replace(" ", " ")
    lines = text.split("\n")

    # 1) Effective date / version.
    version = "unknown"
    date_line = next(
        (l for l in lines if re.search(r"effective as of", l, re.IGNORECASE)), None
    )
    if date_line is not None:
        m = re.search(r"effective as of (.+?)\.?\s*$", date_line, re.IGNORECASE)
        version = m.group(1).strip() if m else date_line.strip()

    # 2) Section boundaries by exact standalone marker lines. The first
    #    "Credits" precedes the numbered rules, "Glossary" starts the
    #    glossary, and the trailing "Credits" ends the glossary.
    first_credits = index_of_line(lines, "Credits", 0)
    glossary_start = index_of_line(lines, "Glossary", first_credits + 1)
    trailing_credits = index_of_line(lines, "Credits", glossary_start + 1)
    if first_credits < 0 or glossary_start < 0 or trailing_credits < 0:
        raise ValueError(
            "could not locate section markers: "
            f"firstCredits={first_credits} glossaryStart={glossary_start} "
            f"trailingCredits={trailing_credits}"
        )

    rules_lines = lines[first_credits + 1 : glossary_start]
    glossary_lines = lines[glossary_start + 1 : trailing_credits]

    # 3) Rules: a rule head starts an entry, following lines are wrapped
    #    continuations, a blank line ends the entry, and non-matching lines
    #    outside an entry (stray section headers) are skipped.
    rules: list[dict] = []
    cur_rule: str | None = None
    cur_parts: list[str] = []

    def flush_rule() -> None:
        nonlocal cur_rule, cur_parts
        if cur_rule is not None:
            rules.append({"rule": cur_rule, "text": clean(" ".join(cur_parts))})
        cur_rule = None
        cur_parts = []

    for raw in rules_lines:
        if raw.strip() == "":
            flush_rule()
            continue
        m = RULE_HEAD.match(raw)
        if m:
            flush_rule()
            cur_rule = m.group(1)
            cur_parts = [m.group(2)]
        elif cur_rule is not None:
            cur_parts.append(raw.strip())
    flush_rule()

    # 4) Glossary: a term line, then definition lines until a blank line.
    glossary: list[dict] = []
    term: str | None = None
    def_parts: list[str] = []

    def flush_term() -> None:
        nonlocal term, def_parts
        if term is not None:
            t = clean(term)
            d = clean(" ".join(def_parts))
            if t:
                glossary.append({"term": t, "text": d})
        term = None
        def_parts = []

    for raw in glossary_lines:
        if raw.strip() == "":
            flush_term()
            continue
        if term is None:
            term = raw
        else:
            def_parts.append(raw.strip())
    flush_term()

    return {
        "version": version,
        "ruleCount": len(rules),
        "glossaryCount": len(glossary),
        "rules": rules,
        "glossary": glossary,
    }


def validate(
    parsed: dict, prev_manifest: dict | None, allow_shrink: bool = False
) -> list[str]:
    errors: list[str] = []

    version = parsed["version"]
    if version == "unknown" or not re.search(r"\d{4}", version):
        errors.append(f"version not parsed (need a 4-digit year): {version!r}")

    if parsed["ruleCount"] < MIN_RULES:
        errors.append(f"ruleCount {parsed['ruleCount']} < {MIN_RULES}")
    if parsed["glossaryCount"] < MIN_GLOSSARY:
        errors.append(f"glossaryCount {parsed['glossaryCount']} < {MIN_GLOSSARY}")

    by_number = {r["rule"]: r["text"] for r in parsed["rules"]}
    for want in REQUIRED_RULES:
        if not by_number.get(want):
            errors.append(f"required rule {want} missing or empty")

    if prev_manifest is not None and not allow_shrink:
        for key, count in (
            ("rule_count", parsed["ruleCount"]),
            ("glossary_count", parsed["glossaryCount"]),
        ):
            prev = prev_manifest.get(key)
            if isinstance(prev, int) and prev > 0 and count < prev * SHRINK_FLOOR:
                errors.append(
                    f"{key} shrank too far: {count} < 95% of previous {prev}"
                )

    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the Comprehensive Rules mirror.")
    ap.add_argument(
        "--force",
        action="store_true",
        help="rebuild even if the effective date is unchanged",
    )
    ap.add_argument(
        "--allow-shrink",
        action="store_true",
        help="skip the previous-manifest shrink comparison (absolute floors still apply)",
    )
    args = ap.parse_args()

    print(f"[cr] fetching rules page {RULES_PAGE}", flush=True)
    page = http_get(RULES_PAGE, timeout=120).decode("utf-8", errors="replace")

    m = TXT_URL_RE.search(page)
    if not m:
        print("[cr] FATAL: no MagicCompRules .txt link found on rules page", file=sys.stderr)
        return 1
    txt_url = m.group(0).replace(" ", "%20")

    dm = re.search(r"(\d{8})\.txt$", txt_url, re.IGNORECASE)
    if not dm:
        print(f"[cr] FATAL: no YYYYMMDD date in txt filename: {txt_url}", file=sys.stderr)
        return 1
    try:
        effective_date = datetime.strptime(dm.group(1), "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        print(f"[cr] FATAL: invalid date {dm.group(1)!r} in txt filename", file=sys.stderr)
        return 1
    print(f"[cr] current CR: {txt_url} (effective {effective_date})", flush=True)

    prev_manifest: dict | None = None
    if MANIFEST_PATH.exists():
        try:
            prev_manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"[cr] WARNING: could not read previous manifest: {e}", flush=True)

    if (
        prev_manifest is not None
        and prev_manifest.get("effective_date") == effective_date
        and not args.force
    ):
        print(
            f"[cr] no change: mirror already at effective_date {effective_date}, "
            "nothing rewritten",
            flush=True,
        )
        emit_github_output("changed", "false")
        return 0

    print(f"[cr] downloading {txt_url}", flush=True)
    raw_bytes = http_get(txt_url, timeout=300)
    print(f"[cr] downloaded {len(raw_bytes):,} bytes", flush=True)
    if len(raw_bytes) < MIN_TXT_BYTES:
        print(
            f"[cr] FATAL: txt too small ({len(raw_bytes):,} bytes < {MIN_TXT_BYTES:,})",
            file=sys.stderr,
        )
        return 1

    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        print(f"[cr] FATAL: response is not valid UTF-8: {e}", file=sys.stderr)
        return 1
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    try:
        parsed = parse_cr(text)
    except ValueError as e:
        print(f"[cr] FATAL: parse failed: {e}", file=sys.stderr)
        return 1
    print(
        f"[cr] parsed version={parsed['version']!r} "
        f"rules={parsed['ruleCount']} glossary={parsed['glossaryCount']}",
        flush=True,
    )

    errors = validate(parsed, prev_manifest, allow_shrink=args.allow_shrink)
    if errors:
        for e in errors:
            print(f"[cr] FATAL: validation failed: {e}", file=sys.stderr)
        print("[cr] existing rules/ files left untouched", file=sys.stderr)
        return 1

    built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sha256_raw = hashlib.sha256(text.encode("utf-8")).hexdigest()
    manifest = {
        "effective_date": effective_date,
        "version": parsed["version"],
        "rule_count": parsed["ruleCount"],
        "glossary_count": parsed["glossaryCount"],
        "source_url": txt_url,
        "built_at": built_at,
        "sha256_raw": sha256_raw,
    }

    RULES_DIR.mkdir(parents=True, exist_ok=True)
    RAW_PATH.write_text(text, encoding="utf-8")
    JSON_PATH.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    MANIFEST_PATH.write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    emit_github_output("changed", "true")

    print(
        f"[cr] DONE effective={effective_date} rules={parsed['ruleCount']} "
        f"glossary={parsed['glossaryCount']} sha256={sha256_raw[:16]}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
