#!/usr/bin/env python3
"""Extract failure/fix signatures from parent agent-transcript JSONL files.

Walks agent-transcripts/<uuid>/<uuid>.jsonl (skips subagents/), parses each line
as JSON, and flags rows whose text matches failure heuristics. Emits
docs/chat_failure_registry.json for human triage.

Usage (from HPCPerfStats/):
  python scripts/extract_chat_failure_signatures.py \\
    --transcripts-dir /path/to/agent-transcripts \\
    --since 2026-01-04 --until 2026-06-04
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

FAILURE_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bFAILED\b",
        r"AssertionError",
        r"\bpytest\b.*\bfail",
        r"\btraceback\b",
        r"\bError:",
        r"failed to resolve host",
        r"npm test",
        r"E2E failed",
        r"\bTypeError\b",
        r"\bIntegrityError\b",
        r"\bTimeoutError\b",
        r"\bregression\b",
        r"fix applied",
        r"\bModuleNotFoundError\b",
        r"\bAttributeError\b",
        r"\bKeyError\b",
        r"playwright",
        r"docker-compose.*fail",
    )
]

FILE_PATH_RE = re.compile(
    r"(?:hpcperfstats|HPCPerfStats|hpcperfstats-tools)[/\\][\w./\\-]+\.(?:py|jsx?|mdc|sh)"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transcripts-dir",
        type=Path,
        required=True,
        help="Root agent-transcripts directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/chat_failure_registry.json"),
        help="Registry JSON output path (relative to cwd)",
    )
    parser.add_argument("--since", default="2026-01-04", help="Inclusive date YYYY-MM-DD")
    parser.add_argument("--until", default="2026-06-04", help="Inclusive date YYYY-MM-DD")
    return parser.parse_args()


def _parse_date(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _in_window(mtime: float, since: datetime, until: datetime) -> bool:
    dt = datetime.fromtimestamp(mtime, tz=timezone.utc)
    return since <= dt <= until.replace(hour=23, minute=59, second=59)


def _text_from_record(obj: dict) -> str:
    parts: list[str] = []
    message = obj.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
        elif isinstance(content, str):
            parts.append(content)
    for key in ("text", "content"):
        if key in obj and isinstance(obj[key], str):
            parts.append(obj[key])
    return "\n".join(parts)


def _matches_failure(text: str) -> list[str]:
    hits = []
    for pat in FAILURE_PATTERNS:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits


def _files_mentioned(text: str) -> list[str]:
    return sorted(set(FILE_PATH_RE.findall(text)))


def _parent_jsonl_paths(root: Path) -> list[Path]:
    paths = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name == "subagents":
            continue
        candidate = child / f"{child.name}.jsonl"
        if candidate.is_file():
            paths.append(candidate)
    return paths


def _scan_transcript(
    path: Path,
    since: datetime,
    until: datetime,
) -> list[dict]:
    mtime = path.stat().st_mtime
    if not _in_window(mtime, since, until):
        return []
    chat_id = path.parent.name
    rows: list[dict] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = _text_from_record(obj)
            if not text:
                continue
            hits = _matches_failure(text)
            if not hits:
                continue
            snippet = text.replace("\n", " ")[:400]
            rows.append({
                "chat_id": chat_id,
                "line": line_no,
                "role": obj.get("role", ""),
                "date": datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d"),
                "patterns": hits,
                "snippet": snippet,
                "files_mentioned": _files_mentioned(text),
                "test_added": "unknown",
                "covered": False,
                "gap_class": "",
                "gap_why_missed": "",
                "rules_updated": [],
                "test_path": "",
            })
    return rows


def _dedupe_rows(rows: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (row["chat_id"], row["snippet"][:120])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def main() -> int:
    args = _parse_args()
    since = _parse_date(args.since)
    until = _parse_date(args.until)
    root = args.transcripts_dir.expanduser().resolve()
    if not root.is_dir():
        raise SystemExit(f"transcripts dir not found: {root}")

    all_rows: list[dict] = []
    for path in _parent_jsonl_paths(root):
        all_rows.extend(_scan_transcript(path, since, until))

    all_rows = _dedupe_rows(all_rows)
    by_chat: dict[str, int] = {}
    for row in all_rows:
        by_chat[row["chat_id"]] = by_chat.get(row["chat_id"], 0) + 1

    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "transcripts_dir": str(root),
        "since": args.since,
        "until": args.until,
        "parent_chats_scanned": len(_parent_jsonl_paths(root)),
        "hit_count": len(all_rows),
        "hits_by_chat": by_chat,
        "rows": all_rows,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"Wrote {args.output} ({len(all_rows)} rows from "
        f"{payload['parent_chats_scanned']} parent chats)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
