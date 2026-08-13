"""Shared helpers for HPCPerfStats Cursor hooks (stdlib only)."""
from __future__ import annotations

import contextlib
import fcntl
import json
import re
from pathlib import Path
from typing import Iterable

EDIT_TOOL_NAMES = frozenset(
    {"Write", "StrReplace", "EditNotebook", "Delete", "ApplyPatch"},
)

READ_TOOL_NAMES = frozenset({"Read", "ReadFile"})

PLAN_TOOL_NAMES = frozenset({"CreatePlan"})

PLAN_TEMPLATE_READ_SUFFIX = "docs/plans/PLAN_TEMPLATE.md"

MONITOR_PLAN_TEMPLATE_SUFFIX = "HPCPerfStats/monitor/docs/plans/PLAN_TEMPLATE.md"

HPCPERFSTATS_ROUTER_BASENAMES = frozenset(
    {
        "agent-discipline-core.mdc",
        "plan-completion-gate.mdc",
        "every-error-regression-test.mdc",
        "workspace-guardrails.mdc",
        "workspace-layout-and-python-env.mdc",
        "RULES_README.md",
    },
)

MONITOR_ROUTER_BASENAMES = frozenset(
    {
        "agent-discipline-core.mdc",
        "plan-completion-gate.mdc",
        "every-error-regression-test.mdc",
        "monitor-workspace-contract.mdc",
        "python-venv-enforcement.mdc",
        "workspace-single-cursor-directory.mdc",
        "out-of-monitor-hpcperfstats-rules.mdc",
        "RULES_README.md",
    },
)

ROUTER_BASENAMES = HPCPERFSTATS_ROUTER_BASENAMES | MONITOR_ROUTER_BASENAMES

PLAN_AUTHORING_REQUIRED_MDC = (
    "plan-creation-contract.mdc",
    "plan-live-disk-sync.mdc",
    "plan-template-enforcement.mdc",
    "compose-operator-terminal-commands.mdc",
    "deploy-ini-with-code-no-phase-zero.mdc",
)

# Full-file Read (no limit/offset) required when Operator discovery needs commands.
OPERATOR_FULL_READ_REQUIRED_MDC = (
    "compose-operator-terminal-commands.mdc",
    "operator-command-lessons-learned.mdc",
)

LIVE_PLAN_DISK_SUFFIX = ".cursor/plans/"

PLAN_CONTENT_SECTIONS = (
    (
        re.compile(r"##\s*Plan disk file", re.I),
        "## Plan disk file (authority — chat does not count)",
    ),
    (
        re.compile(r"##\s*Operator discovery", re.I),
        "## Operator discovery",
    ),
    (
        re.compile(r"##\s*(?:\d+\.\s*)?Problem and facts", re.I),
        "## Problem and facts",
    ),
    (
        re.compile(r"##\s*(?:\d+\.\s*)?Approach", re.I),
        "## Approach",
    ),
    (
        re.compile(r"##\s*(?:\d+\.\s*)?Testing", re.I),
        "## Testing",
    ),
    (
        re.compile(
            r"##\s*(?:\d+\.\s*)?(?:Implementation|Implementation touch list)",
            re.I,
        ),
        "## Implementation",
    ),
    (
        re.compile(r"##\s*(?:\d+\.\s*)?Cursor rules", re.I),
        "## Cursor rules / docs sync",
    ),
    (
        re.compile(r"##\s*Final code review", re.I),
        "## Final code review (mandatory before implementation close)",
    ),
    (
        re.compile(r"##\s*Post-implementation review", re.I),
        "## Post-implementation review (required before close)",
    ),
    (
        re.compile(r"id:\s*git-hooks-pre-close\b", re.I),
        "git-hooks-pre-close todo",
    ),
    (
        re.compile(r"id:\s*post-implementation-review\b", re.I),
        "post-implementation-review todo",
    ),
)

PLAN_CLOSE_RE = re.compile(
    r"\b("
    r"plan (is )?ready|created (a )?plan|present(ing)? (the )?plan|"
    r"confirm the plan|review the plan|plan (authored|delivered)"
    r")\b",
    re.I,
)

AGENT_RULE_DISPATCH_LABEL = "## Agent rule dispatch"
AGENT_RULE_DISPATCH_DETAIL_LABEL = (
    "## Agent rule dispatch (list Read *.mdc rules or N/A with reason)"
)

CLOSE_GATE_SECTIONS = (
    (
        re.compile(r"##\s*Agent rule dispatch", re.I),
        AGENT_RULE_DISPATCH_LABEL,
    ),
    (
        re.compile(r"##\s*Final code review\s*\(senior engineer pass\)", re.I),
        "## Final code review (senior engineer pass)",
    ),
    (
        re.compile(r"##\s*Post-implementation review", re.I),
        "## Post-implementation review",
    ),
    (
        re.compile(r"###\s*Why it works", re.I),
        "### Why it works",
    ),
    (
        re.compile(r"###\s*Edge cases", re.I),
        "### Edge cases",
    ),
    (
        re.compile(r"###\s*Convention check", re.I),
        "### Convention check",
    ),
)

COMPLETION_RE = re.compile(
    r"\b("
    r"done|complete|completed|fixed|ready to ship|ready for merge|"
    r"tests pass|passed|implemented|merge blockers.*none"
    r")\b",
    re.I,
)

# Always-on rules are injected every turn; Read tool proof is not required for them.
READ_VERIFY_EXEMPT_MDC = frozenset(
    name.lower()
    for name in ROUTER_BASENAMES
    if name.endswith(".mdc")
)

DISPATCH_MDC_RE = re.compile(r"([\w.-]+\.mdc)\b", re.I)


def router_from_rule_path(rule_path: str) -> Path | None:
    path = Path(rule_path)
    if "cursor-rules" not in path.parts:
        return None
    router = path.parent / "agent-discipline-core.mdc"
    return router if router.is_file() else None


def _detect_profile(workspace_roots: Iterable[str] | None = None) -> str:
    from hook_task_router import detect_rules_profile  # noqa: PLC0415

    return detect_rules_profile(workspace_roots)


def profile_rules_dir_label(profile: str | None = None, *, workspace_roots: Iterable[str] | None = None) -> str:
    from hook_task_router import profile_rules_dir_label as _label  # noqa: PLC0415

    resolved = profile or _detect_profile(workspace_roots)
    return _label(resolved)


def find_router_file(workspace_roots: Iterable[str], *, rule_path: str = "") -> Path | None:
    from_rule = router_from_rule_path(rule_path)
    if from_rule is not None:
        return from_rule
    candidates = []
    for root in workspace_roots or []:
        base = Path(root)
        candidates.extend(
            [
                base / "HPCPerfStats" / "monitor" / "cursor-rules" / "agent-discipline-core.mdc",
                base / "HPCPerfStats" / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc",
                base / "monitor" / "cursor-rules" / "agent-discipline-core.mdc",
                base / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc",
            ]
        )
    hook_dir = Path(__file__).resolve().parent
    checkout_root = hook_dir.parent
    workspace_root = checkout_root.parent
    candidates.extend(
        [
            checkout_root / "monitor" / "cursor-rules" / "agent-discipline-core.mdc",
            checkout_root / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc",
            workspace_root / "HPCPerfStats" / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc",
        ],
    )
    profile = _detect_profile(workspace_roots)
    preferred = (
        checkout_root / "monitor" / "cursor-rules" / "agent-discipline-core.mdc"
        if profile == "monitor"
        else checkout_root / "hpcperfstats" / "cursor-rules" / "agent-discipline-core.mdc"
    )
    if preferred.is_file():
        return preferred
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def find_hook_task_router_file() -> Path | None:
    candidate = Path(__file__).resolve().parent / "hook_task_router.py"
    return candidate if candidate.is_file() else None


def resolve_cursor_rule_path(rule_basename: str) -> Path | None:
    """Return on-disk path for a domain rule basename, or None when retired/deleted."""
    name = Path(rule_basename or "").name
    if not name.endswith(".mdc"):
        return None
    hook_dir = Path(__file__).resolve().parent
    checkout_root = hook_dir.parent
    workspace_root = checkout_root.parent
    candidates = [
        checkout_root / "monitor" / "cursor-rules" / name,
        checkout_root / "hpcperfstats" / "cursor-rules" / name,
        workspace_root / "HPCPerfStats" / "monitor" / "cursor-rules" / name,
        workspace_root / "HPCPerfStats" / "hpcperfstats" / "cursor-rules" / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def cursor_rule_file_exists(rule_basename: str) -> bool:
    return resolve_cursor_rule_path(rule_basename) is not None


def load_json_stdin() -> dict:
    raw = __import__("sys").stdin.read()
    if not raw.strip():
        return {}
    return json.loads(raw)


def emit_deny(user_message: str, *, agent_message: str | None = None) -> None:
    emit_json(
        {
            "permission": "deny",
            "user_message": user_message,
            "agent_message": agent_message or user_message,
        },
    )


def emit_allow() -> None:
    emit_json({"permission": "allow"})


def emit_json(payload: dict) -> None:
    print(json.dumps(payload))


def parse_transcript_lines(transcript_path: str) -> list[dict]:
    path = Path(transcript_path)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def last_turn_rows(rows: list[dict]) -> list[dict]:
    """Rows after the final user message in the transcript."""
    last_user_idx = -1
    for idx, row in enumerate(rows):
        if row.get("role") == "user":
            last_user_idx = idx
    if last_user_idx < 0:
        return rows
    return rows[last_user_idx + 1 :]


def create_plan_payload_from_tool_part(part: dict) -> dict | None:
    if not isinstance(part, dict):
        return None
    tool_name = None
    payload = None
    if part.get("type") == "tool_use":
        tool_name = part.get("name")
        payload = part.get("input") or {}
    elif part.get("type") == "tool_call":
        tool_name = part.get("tool_name") or part.get("name")
        payload = part.get("input") or part.get("arguments") or {}
    if tool_name not in PLAN_TOOL_NAMES or not isinstance(payload, dict):
        return None
    return payload


def is_create_plan_tool_part(part: dict) -> bool:
    return create_plan_payload_from_tool_part(part) is not None


def extract_create_plan_markdown(rows: list[dict]) -> str:
    chunks: list[str] = []
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            payload = create_plan_payload_from_tool_part(part)
            if not payload:
                continue
            plan_text = payload.get("plan") or payload.get("content") or ""
            if plan_text:
                chunks.append(str(plan_text))
    return "\n".join(chunks)


def paths_from_plan_markdown(text: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    patterns = (
        re.compile(r"`([^`\n]+\.(?:py|mdc|md|ya?ml|jsx?|sh))`", re.I),
        re.compile(r"\]\(([^)\n]+\.(?:py|mdc|md|ya?ml|jsx?|sh))\)", re.I),
    )
    for pattern in patterns:
        for match in pattern.finditer(text or ""):
            raw = match.group(1).strip()
            if not raw or raw in seen:
                continue
            seen.add(raw)
            paths.append(raw)
    return paths


def is_plan_template_read_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/")
    if normalized.endswith(PLAN_TEMPLATE_READ_SUFFIX) or normalized.endswith(
        "PLAN_TEMPLATE.md",
    ):
        return True
    return normalized.endswith(MONITOR_PLAN_TEMPLATE_SUFFIX) or (
        "monitor/docs/plans/PLAN_TEMPLATE.md" in normalized
    )


def tool_name_from_tool_part(part: dict) -> str | None:
    if not isinstance(part, dict):
        return None
    if part.get("type") == "tool_use":
        return part.get("name")
    if part.get("type") == "tool_call":
        return part.get("tool_name") or part.get("name")
    return None


def canonical_tool_name(tool_name: str | None) -> str | None:
    if not tool_name:
        return None
    # Some Cursor tool transcripts include namespaced tool ids.
    base = str(tool_name).rsplit(".", 1)[-1]
    if base in READ_TOOL_NAMES:
        return "Read"
    return base


def edit_payload_from_tool_part(part: dict) -> dict | None:
    if not isinstance(part, dict):
        return None
    tool_name = canonical_tool_name(tool_name_from_tool_part(part))
    if tool_name not in EDIT_TOOL_NAMES:
        return None
    if part.get("type") == "tool_use":
        payload = part.get("input") or {}
    elif part.get("type") == "tool_call":
        payload = part.get("input") or part.get("arguments") or {}
    else:
        return None
    return payload if isinstance(payload, dict) else None


def edit_path_from_tool_part(part: dict) -> str | None:
    payload = edit_payload_from_tool_part(part)
    if not payload:
        return None
    for key in ("path", "file_path", "target_file", "target_notebook"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def is_edit_tool_part(part: dict) -> bool:
    return edit_path_from_tool_part(part) is not None


def turn_had_edits(rows: list[dict]) -> bool:
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            if is_edit_tool_part(part):
                return True
    return False


def turn_had_create_plan(rows: list[dict]) -> bool:
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            if is_create_plan_tool_part(part):
                return True
    return False


def turn_had_closeable_work(rows: list[dict]) -> bool:
    return turn_had_edits(rows) or turn_had_create_plan(rows)


def extract_edited_paths(rows: list[dict]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            path = edit_path_from_tool_part(part)
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def live_plan_disk_paths_from_rows(rows: list[dict]) -> list[str]:
    return [path for path in extract_edited_paths(rows) if is_live_plan_disk_path(path)]


def resolve_live_plan_disk_path(
    plan_path: str,
    workspace_roots: Iterable[str] | None = None,
) -> Path | None:
    """Resolve a live plan path to an on-disk file when it exists."""
    normalized = (plan_path or "").replace("\\", "/")
    if not is_live_plan_disk_path(normalized):
        return None
    direct = Path(normalized)
    if direct.is_file():
        return direct
    hook_dir = Path(__file__).resolve().parent
    checkout_root = hook_dir.parent
    workspace_root = checkout_root.parent
    rel = normalized.lstrip("/")
    candidates = [Path(root) / rel for root in (workspace_roots or [])]
    candidates.extend(
        [
            workspace_root / rel,
            checkout_root.parent / rel,
        ],
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def extract_live_plan_disk_write_contents_from_transcript(rows: list[dict]) -> str:
    """Plan body from Write tool inputs to live plan paths in this turn."""
    last_full = ""
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            path = edit_path_from_tool_part(part)
            if not path or not is_live_plan_disk_path(path):
                continue
            payload = edit_payload_from_tool_part(part)
            if not payload:
                continue
            if tool_name_from_tool_part(part) != "Write":
                continue
            contents = payload.get("contents")
            if isinstance(contents, str) and contents.strip():
                last_full = contents
    return last_full


def extract_plan_authority_markdown(
    rows: list[dict],
    workspace_roots: Iterable[str] | None = None,
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> str:
    """Authoritative plan body for hook validation — disk file, not CreatePlan tool."""
    paths = list(live_plan_disk_paths_from_rows(rows))
    if transcript_path is not None and full_rows is not None:
        for path in live_plan_disk_paths_from_ledger(transcript_path, full_rows):
            if path not in paths:
                paths.append(path)
    for plan_path in reversed(paths):
        resolved = resolve_live_plan_disk_path(plan_path, workspace_roots)
        if resolved is not None:
            text = resolved.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                return text
    return extract_live_plan_disk_write_contents_from_transcript(rows)


def extract_work_paths(
    rows: list[dict],
    workspace_roots: Iterable[str] | None = None,
) -> list[str]:
    paths = extract_edited_paths(rows)
    seen = set(paths)
    plan_md = extract_plan_authority_markdown(rows, workspace_roots)
    if not plan_md.strip():
        plan_md = extract_create_plan_markdown(rows)
    for plan_path in paths_from_plan_markdown(plan_md):
        if plan_path not in seen:
            seen.add(plan_path)
            paths.append(plan_path)
    return paths


def is_live_plan_disk_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/")
    return LIVE_PLAN_DISK_SUFFIX in normalized and normalized.endswith(".plan.md")


def turn_had_live_plan_disk_write(rows: list[dict]) -> bool:
    return any(is_live_plan_disk_path(path) for path in extract_edited_paths(rows))


def suggested_live_plan_disk_path(create_plan_payload: dict | None) -> str:
    """Best-effort live plan path from CreatePlan tool input."""
    payload = create_plan_payload if isinstance(create_plan_payload, dict) else {}
    name = payload.get("name")
    if not name:
        plan_text = str(payload.get("plan") or payload.get("content") or "")
        match = re.search(r"^name:\s*['\"]?([^'\"\n]+)", plan_text, re.M)
        if match:
            name = match.group(1).strip()
    if name:
        kebab = re.sub(r"[^a-z0-9-]+", "-", str(name).strip().lower())
        kebab = re.sub(r"-+", "-", kebab).strip("-")
        if kebab:
            return f"{LIVE_PLAN_DISK_SUFFIX}{kebab}.plan.md"
    return f"{LIVE_PLAN_DISK_SUFFIX}<short-kebab-name>.plan.md"


def plan_disk_sync_followup_message(
    issues: list[str],
    *,
    suggested_path: str = "",
    loop_count: int = 0,
) -> str:
    """User-visible follow-up when a turn used CreatePlan without a disk write."""
    path_hint = suggested_path or f"{LIVE_PLAN_DISK_SUFFIX}<short-kebab-name>.plan.md"
    issue_text = ", ".join(issues) if issues else "Plan not written to disk"
    return (
        "Plan disk sync incomplete (Cursor stop hook). This turn called CreatePlan "
        "but did not Write or StrReplace a live plan under "
        f"<workspace_root>/{path_hint} in the same turn.\n\n"
        "MANDATORY before ending the turn or claiming the plan exists:\n"
        "1. Read plan-creation-contract.mdc, plan-live-disk-sync.mdc, and "
        "docs/plans/PLAN_TEMPLATE.md (Read tool).\n"
        "2. Write the full plan (frontmatter todos + PLAN_TEMPLATE sections) to "
        f"`{path_hint}` — CreatePlan and chat do not count (plan-live-disk-sync.mdc).\n"
        "3. Include ## Plan disk file with live path and last-updated date.\n\n"
        f"Missing now: {issue_text}\n"
        f"loop_count={loop_count}. Per plan-live-disk-sync.mdc and plan-creation-contract.mdc."
    )


def create_plan_disk_sync_post_tool_context(suggested_path: str) -> str:
    """Inject immediately after CreatePlan so the agent writes disk in the same turn."""
    return (
        "PLAN DISK SYNC (mandatory same turn): CreatePlan does NOT satisfy "
        "plan-live-disk-sync.mdc. preToolUse will DENY other tools until you "
        "Write or StrReplace the full plan to "
        f"<workspace_root>/{suggested_path} "
        "(frontmatter todos + PLAN_TEMPLATE sections including ## Plan disk file; "
        "operator commands only under ## Operator discovery → ### Pending commands). "
        "Read plan-creation-contract.mdc, plan-live-disk-sync.mdc, "
        "compose-operator-terminal-commands.mdc, deploy-ini-with-code-no-phase-zero.mdc, "
        "plan-template-enforcement.mdc, and PLAN_TEMPLATE.md first if not already Read. "
        "Chat and CreatePlan alone are not authoritative."
    )


def iter_tool_parts(rows: list[dict]) -> Iterable[tuple[int, dict]]:
    for event_idx, row in enumerate(rows):
        if row.get("role") != "assistant":
            continue
        message = row.get("message") or {}
        for part in message.get("content") or []:
            if isinstance(part, dict):
                yield event_idx, part


def first_edit_event_index(rows: list[dict]) -> int | None:
    for event_idx, part in iter_tool_parts(rows):
        if is_edit_tool_part(part):
            return event_idx
    return None


def first_closeable_event_index(rows: list[dict]) -> int | None:
    first: int | None = None
    for event_idx, part in iter_tool_parts(rows):
        if is_edit_tool_part(part) or is_create_plan_tool_part(part):
            if first is None or event_idx < first:
                first = event_idx
    return first


def read_event_indices_for_rule(rows: list[dict], rule_basename: str) -> list[int]:
    target = rule_basename.lower()
    indices: list[int] = []
    for event_idx, part in iter_tool_parts(rows):
        path = read_path_from_tool_part(part)
        if path and is_cursor_rule_read_path(path) and Path(path).name.lower() == target:
            indices.append(event_idx)
    return indices


def extract_assistant_text(rows: list[dict]) -> str:
    chunks: list[str] = []
    for row in reversed(rows):
        if row.get("role") != "assistant":
            continue
        message = row.get("message") or {}
        for part in message.get("content") or []:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text") or ""
                if text:
                    chunks.append(text)
        if chunks:
            break
    return "\n".join(reversed(chunks))


def section_body_after_heading(text: str, heading_match: re.Match[str]) -> str:
    rest = text[heading_match.end() :]
    next_h2 = re.search(r"\n##\s+", rest)
    if next_h2:
        return rest[: next_h2.start()]
    return rest


def extract_subsection_h3(section_body: str, heading: str) -> str:
    match = re.search(rf"###\s*{re.escape(heading)}\b", section_body or "", re.I)
    if not match:
        return ""
    rest = section_body[match.end() :]
    next_h3 = re.search(r"\n###\s+", rest)
    if next_h3:
        return rest[: next_h3.start()]
    return rest


OPERATOR_DISCOVERY_STATUS_RE = re.compile(
    r"\*\*Status:\*\*\s*`?(not needed|in progress|complete)`?",
    re.I,
)


def extract_operator_discovery_section(plan_markdown: str) -> str:
    match = re.search(r"##\s*Operator discovery\b", plan_markdown or "", re.I)
    if not match:
        return ""
    return section_body_after_heading(plan_markdown, match)


def operator_discovery_status(section_body: str) -> str | None:
    match = OPERATOR_DISCOVERY_STATUS_RE.search(section_body or "")
    if not match:
        return None
    return match.group(1).lower().replace("`", "").strip()


def _pending_has_bash_blocks(text: str) -> bool:
    return bool(re.search(r"```bash\b", text or "", re.I))


_COMPOSE_SUBCMD_RE = re.compile(
    r"docker(?:-compose)?\s+compose\s+"
    r"(?:(?:-p|--project-name|-f|--file)\s+\S+\s+)*"
    r"(exec|run|logs)\b",
    re.I,
)
# Legacy `docker-compose` (hyphen) without the `compose` token.
_COMPOSE_HYPHEN_SUBCMD_RE = re.compile(
    r"docker-compose\s+"
    r"(?:(?:-p|--project-name|-f|--file)\s+\S+\s+)*"
    r"(exec|run|logs)\b",
    re.I,
)


def _bash_has_compose_subcommand(bash_body: str) -> bool:
    return bool(
        _COMPOSE_SUBCMD_RE.search(bash_body or "")
        or _COMPOSE_HYPHEN_SUBCMD_RE.search(bash_body or "")
    )


def _validate_pending_commands_subsection(pending: str) -> list[str]:
    """Content-level Pending commands checks (not Read-only / shallow shape).

    Enforces compose-operator-terminal-commands.mdc anti-patterns: one
    #### block per service, no host ``cd`` before docker, no ``--tail``/
    ``--since`` before grep on compose logs, no unfiltered logs firehose,
    no heredoc python through exec, and allow ``-p``/``-f`` before subcommand.
    """
    if not (pending or "").strip():
        return ["Operator discovery: ### Pending commands empty while Status in progress"]
    service_sections = [
        section.strip()
        for section in re.split(r"(?=####\s+\S)", pending)
        if section.strip().startswith("####")
    ]
    if not service_sections:
        return [
            "Operator discovery: ### Pending commands needs service-labeled "
            "#### <service> — <what to paste> headers "
            "(compose-operator-terminal-commands.mdc)",
        ]
    issues: list[str] = []
    seen_services: dict[str, int] = {}
    for section in service_sections:
        label_match = re.match(r"####\s*(\S+)", section)
        if not label_match:
            continue
        service = label_match.group(1).rstrip("—-").strip()
        # Normalize "pipeline — paste …" → service token before em-dash/space.
        service = re.split(r"[\s—-]", service, maxsplit=1)[0]
        seen_services[service] = seen_services.get(service, 0) + 1
        if not re.search(r"```bash\b", section, re.I):
            issues.append(
                f"Operator discovery: #### {service} missing fenced bash block",
            )
            continue
        bash_match = re.search(r"```bash\s*\n(.*?)```", section, re.S | re.I)
        if not bash_match:
            continue
        bash_body = bash_match.group(1)
        if not _bash_has_compose_subcommand(bash_body):
            issues.append(
                f"Operator discovery: #### {service} bash block must use "
                "docker compose exec|run|logs "
                "(flags -p/-f allowed before subcommand)",
            )
            continue
        # Host cd before docker compose (container-internal cd via su/sh -lc is OK).
        for line in bash_body.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if re.match(r"^cd\s+", stripped) and not re.search(
                r"docker\s+compose|docker-compose",
                stripped,
                re.I,
            ):
                issues.append(
                    f"Operator discovery: #### {service} must not use host "
                    "cd before docker compose "
                    "(compose-operator-terminal-commands.mdc)",
                )
                break
        # --tail / --since on compose logs before a pipe to grep.
        for line in bash_body.splitlines():
            if not re.search(r"docker(?:-compose)?\s+(?:compose\s+)?.*\blogs\b", line, re.I):
                if not re.search(r"docker-compose\s+.*\blogs\b", line, re.I):
                    continue
            if re.search(r"--tail(=|\s)|--since(=|\s)", line, re.I):
                # Allow only if this logs line already pipes to grep on same line
                # after the flag (rare); reject the common anti-pattern.
                if not re.search(r"\|\s*grep\b", line, re.I):
                    issues.append(
                        f"Operator discovery: #### {service} must not use "
                        "--tail/--since on docker compose logs before grep "
                        "(compose-operator-terminal-commands.mdc)",
                    )
                    break
                # Flag appears before grep on the same line → still reject.
                flag_pos = re.search(r"--tail(=|\s)|--since(=|\s)", line, re.I)
                grep_pos = re.search(r"\|\s*grep\b", line, re.I)
                if flag_pos and grep_pos and flag_pos.start() < grep_pos.start():
                    issues.append(
                        f"Operator discovery: #### {service} must not use "
                        "--tail/--since on docker compose logs before grep "
                        "(compose-operator-terminal-commands.mdc)",
                    )
                    break
        # Unfiltered compose logs (no grep in the bash block).
        if re.search(
            r"docker(?:-compose)?\s+(?:compose\s+)?(?:(?:-p|--project-name|-f|--file)\s+\S+\s+)*logs\b"
            r"|docker-compose\s+(?:(?:-p|--project-name|-f|--file)\s+\S+\s+)*logs\b",
            bash_body,
            re.I,
        ) and not re.search(r"\bgrep\b", bash_body, re.I):
            issues.append(
                f"Operator discovery: #### {service} docker compose logs must "
                "pipe to grep (no unfiltered firehose) "
                "(compose-operator-terminal-commands.mdc)",
            )
        # Heredoc python through exec (podman REPL anti-pattern).
        if re.search(
            r"python3?\s+-?\s*<<|python3?\s+<<",
            bash_body,
            re.I,
        ) and re.search(r"\bexec\b", bash_body, re.I):
            issues.append(
                f"Operator discovery: #### {service} must not use heredoc "
                "python3 - << through docker compose exec "
                "(operator-command-lessons-learned.mdc)",
            )
        # Hardcoded data roots without conf_parser getters.
        has_conf_parser = bool(
            re.search(
                r"conf_parser|get_archive_dir_path|get_daily_archive_dir_path|"
                r"get_accounting_path",
                bash_body,
                re.I,
            )
        )
        if re.search(
            r"/(?:hpcperfstats|opt/hpcperfstats_data)(?:/|\b)",
            bash_body,
            re.I,
        ) and not has_conf_parser:
            issues.append(
                f"Operator discovery: #### {service} must not hardcode "
                "/hpcperfstats/ (or /opt/hpcperfstats_data/) data roots without "
                "conf_parser getters "
                "(compose-operator-terminal-commands.mdc)",
            )
        # Raw ConfigParser for archive_dir / daily_archive_dir / acct_path.
        if re.search(r"\bConfigParser\b", bash_body) and re.search(
            r"hpcperfstats\.ini|archive_dir|daily_archive_dir|acct_path",
            bash_body,
            re.I,
        ) and not has_conf_parser:
            issues.append(
                f"Operator discovery: #### {service} must use "
                "hpcperfstats.dbload.lib.conf_parser getters for archive_dir "
                "(not raw ConfigParser) "
                "(compose-operator-terminal-commands.mdc)",
            )
    for service, count in seen_services.items():
        if count > 1:
            issues.append(
                f"Operator discovery: #### {service} appears {count} times under "
                "Pending commands — combine into one block per service "
                "(compose-operator-terminal-commands.mdc)",
            )
    return issues


def reconstruct_live_plan_markdown_from_tool_input(
    tool_name: str,
    tool_input: dict,
    *,
    on_disk_path: str | None = None,
) -> str | None:
    """Rebuild proposed live-plan markdown for preToolUse deny checks."""
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Write":
        contents = tool_input.get("contents")
        return str(contents) if contents is not None else None
    if tool_name != "StrReplace":
        return None
    path = on_disk_path
    if not path:
        for key in ("path", "file_path", "target_file"):
            candidate = tool_input.get(key)
            if isinstance(candidate, str) and candidate.strip():
                path = candidate
                break
    if not path:
        return None
    old = tool_input.get("old_string")
    new = tool_input.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return None
    try:
        current = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    if old not in current:
        return None
    replace_all = bool(tool_input.get("replace_all"))
    if replace_all:
        return current.replace(old, new)
    return current.replace(old, new, 1)


def operator_commands_outside_discovery_issues(plan_markdown: str) -> list[str]:
    """docker compose bash blocks must live under Operator discovery → Pending commands."""
    od_match = re.search(r"##\s*Operator discovery\b", plan_markdown or "", re.I)
    if not od_match:
        return []
    rest = plan_markdown[od_match.end() :]
    next_h2 = re.search(r"\n##\s+", rest)
    od_end = od_match.end() + next_h2.start() if next_h2 else len(plan_markdown)
    outside = plan_markdown[: od_match.start()] + plan_markdown[od_end:]
    for block in re.findall(r"```bash\s*\n(.*?)```", outside, re.S | re.I):
        if re.search(r"docker\s+compose\s+(exec|run|logs)", block, re.I):
            return [
                "Operator commands: docker compose blocks must live under "
                "## Operator discovery → ### Pending commands only "
                "(compose-operator-terminal-commands.mdc)",
            ]
    return []


def operator_discovery_issues(plan_markdown: str) -> list[str]:
    section = extract_operator_discovery_section(plan_markdown)
    if not section:
        return []
    status = operator_discovery_status(section)
    if status is None:
        return [
            "Operator discovery: **Status:** `not needed`|`in progress`|`complete` required",
        ]
    issues: list[str] = []
    has_pending = bool(re.search(r"###\s*Pending commands\b", section, re.I))
    has_findings = bool(re.search(r"###\s*Completed findings\b", section, re.I))
    pending_body = extract_subsection_h3(section, "Pending commands")

    if status == "not needed":
        if has_pending and _pending_has_bash_blocks(pending_body):
            issues.append(
                "Operator discovery: Status not needed but Pending commands has bash blocks",
            )
        issues.extend(operator_commands_outside_discovery_issues(plan_markdown))
        return issues

    if status in ("in progress", "complete") and not has_findings:
        issues.append("Operator discovery: ### Completed findings required")

    if status == "in progress":
        if not has_pending:
            issues.append(
                "Operator discovery: ### Pending commands required when Status in progress",
            )
        else:
            issues.extend(_validate_pending_commands_subsection(pending_body))

    if status == "complete" and has_pending and _pending_has_bash_blocks(pending_body):
        issues.append(
            "Operator discovery: Status complete but Pending commands still has bash blocks",
        )

    issues.extend(operator_commands_outside_discovery_issues(plan_markdown))
    return issues


def turn_create_plan_pending_disk_write(
    rows: list[dict],
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> bool:
    """True when CreatePlan ran this turn and no same-turn live plan disk write exists.

    Disk-first is allowed: any Write/StrReplace to ``.cursor/plans/*.plan.md`` in
    the same turn (before or after CreatePlan) clears the pending gate.
    Transcript facts are OR'd with the turn-activity ledger (lag-safe).
    """
    had_create = turn_had_create_plan_union(rows, transcript_path, full_rows)
    had_disk = turn_had_live_plan_disk_write_union(rows, transcript_path, full_rows)
    return had_create and not had_disk


def plan_disk_sync_issues(
    transcript_rows: list[dict],
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> list[str]:
    """CreatePlan or plan-close turns must also Write/StrReplace a .cursor/plans/*.plan.md file."""
    had_create_plan = turn_had_create_plan_union(
        transcript_rows, transcript_path, full_rows,
    )
    had_disk = turn_had_live_plan_disk_write_union(
        transcript_rows, transcript_path, full_rows,
    )
    if had_create_plan and not had_disk:
        return [
            "Plan not written to disk: Write or StrReplace "
            "<workspace>/.cursor/plans/*.plan.md required; CreatePlan/chat alone does not count "
            "(plan-live-disk-sync.mdc)",
        ]
    return []


def plan_authoring_precreate_read_issues(
    transcript_rows: list[dict],
    extra_read_basenames: Iterable[str] = (),
    extra_has_template: bool = False,
) -> list[str]:
    """Required Read tool calls before the first CreatePlan in this turn.

    ``extra_read_basenames`` / ``extra_has_template`` come from the rule-read
    ledger and cover this turn's Reads that the lagging transcript has not yet
    persisted (see ``record_rule_read_to_ledger``).
    """
    issues = domain_rule_read_issues(
        list(PLAN_AUTHORING_REQUIRED_MDC),
        transcript_rows,
        extra_read_basenames=extra_read_basenames,
    )
    issues.extend(
        plan_template_read_issues(transcript_rows, extra_has_template=extra_has_template),
    )
    return issues


def is_allowed_tool_while_plan_disk_pending(tool_name: str, tool_input: dict) -> bool:
    if canonical_tool_name(tool_name) == "Read":
        return True
    if tool_name in ("Write", "StrReplace"):
        for key in ("path", "file_path", "target_file"):
            path = tool_input.get(key)
            if isinstance(path, str) and is_live_plan_disk_path(path):
                return True
    return False


def dispatch_is_na(body: str) -> bool:
    return bool(
        re.search(
            r"\bN/A\b|none triggered|no (domain |triggered )?rules\b",
            body or "",
            re.I,
        )
    )


def agent_rule_dispatch_body_ok(body: str) -> bool:
    if not (body or "").strip():
        return False
    if dispatch_is_na(body):
        return True
    return bool(re.search(r"\.mdc\b", body))


def agent_rule_dispatch_body(text: str) -> str | None:
    match = re.search(r"##\s*Agent rule dispatch", text or "", re.I)
    if not match:
        return None
    return section_body_after_heading(text, match)


def extract_dispatch_listed_mdc(body: str) -> list[str]:
    """Basenames listed under Agent rule dispatch (deduped, preserves first spelling)."""
    if dispatch_is_na(body):
        return []
    seen: dict[str, str] = {}
    for match in DISPATCH_MDC_RE.finditer(body or ""):
        name = match.group(1)
        key = name.lower()
        if key not in seen:
            seen[key] = name
    return list(seen.values())


def read_input_from_tool_part(part: dict) -> dict | None:
    if not isinstance(part, dict):
        return None
    tool_name = None
    payload = None
    if part.get("type") == "tool_use":
        tool_name = part.get("name")
        payload = part.get("input") or {}
    elif part.get("type") == "tool_call":
        tool_name = part.get("tool_name") or part.get("name")
        payload = part.get("input") or part.get("arguments") or {}
    if canonical_tool_name(tool_name) != "Read" or not isinstance(payload, dict):
        return None
    return payload


def read_path_from_tool_part(part: dict) -> str | None:
    payload = read_input_from_tool_part(part)
    if not payload:
        return None
    path = payload.get("path") or payload.get("file_path") or payload.get("target_file")
    return str(path) if path else None


def is_full_file_rule_read(part: dict, rule_basename: str) -> bool:
    """True when Read opens the rule with no limit and no offset."""
    path = read_path_from_tool_part(part)
    if not path or not is_cursor_rule_read_path(path):
        return False
    if Path(path).name.lower() != rule_basename.lower():
        return False
    payload = read_input_from_tool_part(part) or {}
    if payload.get("limit") is not None:
        return False
    if payload.get("offset") is not None:
        return False
    return True


def turn_had_full_file_rule_read(rows: list[dict], rule_basename: str) -> bool:
    for _event_idx, part in iter_tool_parts(rows):
        if is_full_file_rule_read(part, rule_basename):
            return True
    return False


def operator_discovery_needs_full_rule_reads(plan_markdown: str) -> bool:
    """True when Operator discovery is in progress or Pending has bash blocks."""
    section = extract_operator_discovery_section(plan_markdown)
    if not section:
        return False
    status = operator_discovery_status(section)
    pending = extract_subsection_h3(section, "Pending commands")
    if _pending_has_bash_blocks(pending):
        return True
    return status == "in progress"


def full_file_rule_read_issues(
    rows: list[dict],
    basenames: Iterable[str] = OPERATOR_FULL_READ_REQUIRED_MDC,
    extra_full_file_basenames: Iterable[str] = (),
) -> list[str]:
    """Require full-file Reads (no limit/offset); partial Read does not count.

    ``extra_full_file_basenames`` come from the rule-read ledger for this turn's
    full-file Reads the lagging transcript has not yet persisted.
    """
    extra = {str(name).lower() for name in extra_full_file_basenames}
    issues: list[str] = []
    for name in basenames:
        if turn_had_full_file_rule_read(rows, name):
            continue
        if name.lower() in extra:
            continue
        if read_event_indices_for_rule(rows, name):
            issues.append(
                f"Partial Read of {name} does not count — Read the full file "
                "(no limit/offset) when Operator discovery needs commands",
            )
        else:
            issues.append(
                f"Full-file Read required (no limit/offset): {name} "
                "(Operator discovery needs commands; partial Read does not count)",
            )
    return issues


def is_cursor_rule_read_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/")
    if not normalized.endswith(".mdc"):
        return False
    return (
        "hpcperfstats/cursor-rules/" in normalized
        or "monitor/cursor-rules/" in normalized
        # Workspace exposes rules via the `.cursor/rules/` symlink -> the
        # authoritative `hpcperfstats/cursor-rules/` dir; reads opened through
        # that symlink path must count the same as the canonical path.
        or ".cursor/rules/" in normalized
    )


# --- Turn-activity ledger ---------------------------------------------------
# Cursor writes the live turn's tool calls to ``transcript_path`` only after the
# turn ends, so mid-turn ``preToolUse`` / early ``stop`` cannot see this turn's
# Read / CreatePlan / live-plan Write parts in the transcript alone. A
# ``preToolUse`` recorder persists those events under ``fcntl.flock`` so gates
# can union the ledger with the lagging transcript. Turn key =
# ``transcript_user_row_count`` (stable mid-turn).
#
# Primary sidecar: ``.hpc_turn_activity.jsonl``. Legacy ``.hpc_rule_reads.jsonl``
# is still read for compatibility (entries without ``kind`` count as reads).
TURN_ACTIVITY_LEDGER_SUFFIX = ".hpc_turn_activity.jsonl"
RULE_READ_LEDGER_SUFFIX = ".hpc_rule_reads.jsonl"  # legacy alias

LEDGER_KIND_READ = "read"
LEDGER_KIND_CREATE_PLAN = "create_plan"
LEDGER_KIND_LIVE_PLAN_WRITE = "live_plan_write"

_LEDGER_COMPACT_LINE_BUDGET = 400


def turn_activity_ledger_path(transcript_path: str | None) -> Path | None:
    if not transcript_path:
        return None
    return Path(str(transcript_path) + TURN_ACTIVITY_LEDGER_SUFFIX)


def rule_read_ledger_path(transcript_path: str | None) -> Path | None:
    """Primary turn-activity ledger path (legacy name kept for callers/tests)."""
    return turn_activity_ledger_path(transcript_path)


def legacy_rule_read_ledger_path(transcript_path: str | None) -> Path | None:
    if not transcript_path:
        return None
    return Path(str(transcript_path) + RULE_READ_LEDGER_SUFFIX)


def transcript_user_row_count(rows: list[dict]) -> int:
    """Turn marker: user rows persisted in the transcript (stable mid-turn)."""
    return sum(
        1 for row in rows if isinstance(row, dict) and row.get("role") == "user"
    )


def _ledger_entry_kind(entry: dict) -> str:
    kind = entry.get("kind")
    if isinstance(kind, str) and kind.strip():
        return kind.strip()
    return LEDGER_KIND_READ


@contextlib.contextmanager
def _with_ledger_lock(ledger: Path):
    """Exclusive flock around ledger read-modify-write / append."""
    lock_path = Path(str(ledger) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_f:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except OSError:
        # Fail open for lock acquisition — still attempt the body unlocked so a
        # single-writer path keeps working; parallel races may remain if lock fails.
        yield


def _parse_ledger_text(raw: str) -> list[dict]:
    entries: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            entries.append(obj)
    return entries


def _read_ledger_entries(ledger: Path) -> list[dict]:
    if not ledger.is_file():
        return []
    try:
        raw = ledger.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _parse_ledger_text(raw)


def _read_all_ledger_files(transcript_path: str | None) -> list[dict]:
    """Entries from primary + legacy sidecars (no turn filter)."""
    entries: list[dict] = []
    primary = turn_activity_ledger_path(transcript_path)
    legacy = legacy_rule_read_ledger_path(transcript_path)
    for path in (primary, legacy):
        if path is None:
            continue
        entries.extend(_read_ledger_entries(path))
    return entries


def _dedupe_ledger_entries(entries: list[dict]) -> list[dict]:
    """Stable dedupe by (kind, basename|path|name) within a turn."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for entry in entries:
        kind = _ledger_entry_kind(entry)
        key_tail = (
            str(entry.get("basename") or "")
            or str(entry.get("path") or "")
            or str(entry.get("name") or "")
            or json.dumps(entry, sort_keys=True)
        ).lower()
        key = (kind, key_tail)
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _append_ledger_entry(ledger: Path, entry: dict, user_rows: int) -> bool:
    """Append ``entry`` under flock; keep only current-turn lines (compact)."""
    try:
        with _with_ledger_lock(ledger):
            kept = [
                e
                for e in _read_ledger_entries(ledger)
                if int(e.get("user_rows", -1)) == user_rows
            ]
            kept.append(entry)
            if len(kept) > _LEDGER_COMPACT_LINE_BUDGET:
                kept = _dedupe_ledger_entries(kept)
            text = "".join(json.dumps(e) + "\n" for e in kept)
            tmp = Path(str(ledger) + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(ledger)
        return True
    except OSError:
        return False


def _tool_input_path(tool_input: dict) -> str:
    for key in ("path", "file_path", "target_file", "target_notebook"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def record_turn_activity(
    transcript_path: str | None,
    tool_name: str | None,
    tool_input: dict,
) -> bool:
    """Persist Read / CreatePlan / live-plan Write for the current turn.

    Returns True when an entry was written. Uses ``fcntl.flock`` so parallel
    preToolUse recorders cannot drop each other's entries.
    """
    if not isinstance(tool_input, dict):
        return False
    ledger = turn_activity_ledger_path(transcript_path)
    if ledger is None or not transcript_path:
        return False
    user_rows = transcript_user_row_count(
        parse_transcript_lines(str(transcript_path)),
    )
    name = canonical_tool_name(tool_name)

    if name == "Read":
        path = _tool_input_path(tool_input)
        if not path:
            return False
        is_template = is_plan_template_read_path(path)
        if not (is_cursor_rule_read_path(path) or is_template):
            return False
        entry = {
            "kind": LEDGER_KIND_READ,
            "basename": Path(path).name.lower(),
            "template": bool(is_template),
            "partial": tool_input.get("limit") is not None
            or tool_input.get("offset") is not None,
            "user_rows": user_rows,
        }
        return _append_ledger_entry(ledger, entry, user_rows)

    if name == "CreatePlan":
        plan_name = tool_input.get("name")
        entry = {
            "kind": LEDGER_KIND_CREATE_PLAN,
            "name": str(plan_name).strip() if plan_name else "",
            "user_rows": user_rows,
        }
        return _append_ledger_entry(ledger, entry, user_rows)

    if name in ("Write", "StrReplace"):
        path = _tool_input_path(tool_input)
        if not path or not is_live_plan_disk_path(path):
            return False
        entry = {
            "kind": LEDGER_KIND_LIVE_PLAN_WRITE,
            "path": path.replace("\\", "/"),
            "user_rows": user_rows,
        }
        return _append_ledger_entry(ledger, entry, user_rows)

    return False


def record_rule_read_to_ledger(
    transcript_path: str | None,
    tool_name: str | None,
    tool_input: dict,
) -> bool:
    """Persist a cursor-rule / PLAN_TEMPLATE Read (compat wrapper)."""
    return record_turn_activity(transcript_path, tool_name, tool_input)


def _ledger_entries_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> list[dict]:
    """Ledger entries recorded during the current turn (deduped).

    ``full_rows`` must be the complete transcript (not ``last_turn_rows``) so the
    user-row turn marker matches what ``record_turn_activity`` computed.
    """
    if transcript_path is None:
        return []
    current = transcript_user_row_count(full_rows)
    entries = [
        e
        for e in _read_all_ledger_files(transcript_path)
        if int(e.get("user_rows", -1)) == current
    ]
    return _dedupe_ledger_entries(entries)


def ledger_read_basenames_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> set[str]:
    return {
        str(e.get("basename", "")).lower()
        for e in _ledger_entries_this_turn(transcript_path, full_rows)
        if _ledger_entry_kind(e) == LEDGER_KIND_READ and e.get("basename")
    }


def ledger_has_template_read_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> bool:
    return any(
        e.get("template") and _ledger_entry_kind(e) == LEDGER_KIND_READ
        for e in _ledger_entries_this_turn(transcript_path, full_rows)
    )


def ledger_full_file_basenames_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> set[str]:
    return {
        str(e.get("basename", "")).lower()
        for e in _ledger_entries_this_turn(transcript_path, full_rows)
        if (
            _ledger_entry_kind(e) == LEDGER_KIND_READ
            and e.get("basename")
            and not e.get("partial")
        )
    }


def ledger_had_create_plan_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> bool:
    return any(
        _ledger_entry_kind(e) == LEDGER_KIND_CREATE_PLAN
        for e in _ledger_entries_this_turn(transcript_path, full_rows)
    )


def ledger_had_live_plan_write_this_turn(
    transcript_path: str | None,
    full_rows: list[dict],
) -> bool:
    return any(
        _ledger_entry_kind(e) == LEDGER_KIND_LIVE_PLAN_WRITE
        for e in _ledger_entries_this_turn(transcript_path, full_rows)
    )


def live_plan_disk_paths_from_ledger(
    transcript_path: str | None,
    full_rows: list[dict],
) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for entry in _ledger_entries_this_turn(transcript_path, full_rows):
        if _ledger_entry_kind(entry) != LEDGER_KIND_LIVE_PLAN_WRITE:
            continue
        path = str(entry.get("path") or "").replace("\\", "/")
        if path and is_live_plan_disk_path(path) and path not in seen:
            seen.add(path)
            paths.append(path)
    return paths


def turn_had_create_plan_union(
    rows: list[dict],
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> bool:
    if turn_had_create_plan(rows):
        return True
    if transcript_path is not None and full_rows is not None:
        return ledger_had_create_plan_this_turn(transcript_path, full_rows)
    return False


def turn_had_live_plan_disk_write_union(
    rows: list[dict],
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> bool:
    if turn_had_live_plan_disk_write(rows):
        return True
    if transcript_path is not None and full_rows is not None:
        return ledger_had_live_plan_write_this_turn(transcript_path, full_rows)
    return False


def extract_read_rule_basenames(rows: list[dict]) -> set[str]:
    """Lowercase basenames of cursor-rules/*.mdc opened via Read in transcript rows."""
    read_names: set[str] = set()
    for row in rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            path = read_path_from_tool_part(part)
            if path and is_cursor_rule_read_path(path):
                read_names.add(Path(path).name.lower())
    return read_names


def _import_triggered_rules_for_paths():
    from hook_task_router import triggered_rules_for_paths  # noqa: PLC0415

    return triggered_rules_for_paths


def domain_rules_required(assistant_text: str, edited_paths: list[str]) -> list[str]:
    triggered_rules_for_paths = _import_triggered_rules_for_paths()
    triggered = triggered_rules_for_paths(edited_paths)
    body = agent_rule_dispatch_body(assistant_text) or ""
    listed = extract_dispatch_listed_mdc(body)
    seen: dict[str, str] = {}
    for name in [*triggered, *listed]:
        key = name.lower()
        if key in READ_VERIFY_EXEMPT_MDC:
            continue
        if key not in seen:
            seen[key] = name
    return list(seen.values())


def triggered_rule_dispatch_issues(
    assistant_text: str,
    edited_paths: list[str],
) -> list[str]:
    triggered_rules_for_paths = _import_triggered_rules_for_paths()
    domain_triggered = [
        name
        for name in triggered_rules_for_paths(edited_paths)
        if name.lower() not in READ_VERIFY_EXEMPT_MDC
    ]
    if not domain_triggered:
        return []
    body = agent_rule_dispatch_body(assistant_text)
    if body is None:
        return []
    if dispatch_is_na(body):
        return ["Agent rule dispatch (N/A invalid — edits triggered domain rules)"]
    listed_lower = {name.lower() for name in extract_dispatch_listed_mdc(body)}
    issues: list[str] = []
    for rule in sorted(domain_triggered, key=str.lower):
        if rule.lower() not in listed_lower:
            issues.append(f"Rule not dispatched: {rule}")
    return issues


def plan_template_read_event_indices(rows: list[dict]) -> list[int]:
    indices: list[int] = []
    for event_idx, part in iter_tool_parts(rows):
        path = read_path_from_tool_part(part)
        if path and is_plan_template_read_path(path):
            indices.append(event_idx)
    return indices


def plan_template_read_issues(
    transcript_rows: list[dict],
    extra_has_template: bool = False,
) -> list[str]:
    first_closeable = first_closeable_event_index(transcript_rows)
    read_indices = plan_template_read_event_indices(transcript_rows)
    if not read_indices:
        if extra_has_template:
            return []
        return ["PLAN_TEMPLATE.md not read via Read tool"]
    if first_closeable is not None and min(read_indices) > first_closeable:
        return ["PLAN_TEMPLATE.md read after CreatePlan"]
    return []


def plan_authoring_required_mdc_rules(work_paths: list[str]) -> list[str]:
    triggered_rules_for_paths = _import_triggered_rules_for_paths()
    seen: dict[str, str] = {}
    for rule in [*PLAN_AUTHORING_REQUIRED_MDC, *triggered_rules_for_paths(work_paths)]:
        key = rule.lower()
        if key in READ_VERIFY_EXEMPT_MDC:
            continue
        if key not in seen:
            seen[key] = rule
    return list(seen.values())


def plan_content_issues(plan_markdown: str) -> list[str]:
    if not (plan_markdown or "").strip():
        return ["Plan disk file missing or empty"]
    missing: list[str] = []
    for pattern, label in PLAN_CONTENT_SECTIONS:
        if not pattern.search(plan_markdown):
            missing.append(label)
    missing.extend(operator_discovery_issues(plan_markdown))
    missing.extend(sync_timedb_plan_todo_issues(plan_markdown))
    return missing


def domain_rule_read_issues(
    required_rules: list[str],
    transcript_rows: list[dict],
    extra_read_basenames: Iterable[str] = (),
) -> list[str]:
    first_closeable = first_closeable_event_index(transcript_rows)
    extra = {str(name).lower() for name in extra_read_basenames}
    issues: list[str] = []
    for rule in sorted(required_rules, key=str.lower):
        if rule.lower() in READ_VERIFY_EXEMPT_MDC:
            continue
        if not cursor_rule_file_exists(rule):
            continue
        read_indices = read_event_indices_for_rule(transcript_rows, rule)
        if read_indices:
            if first_closeable is not None and min(read_indices) > first_closeable:
                issues.append(f"Rule read after first edit/plan: {rule}")
            continue
        # Ledger fallback: Read happened this turn but the transcript file has
        # not persisted it yet (record_rule_read_to_ledger). preToolUse fires
        # before any edit, so there is no ordering to enforce here.
        if rule.lower() in extra:
            continue
        issues.append(f"Rule not read: {rule}")
    return issues


def edge_cases_issues(assistant_text: str) -> list[str]:
    match = re.search(r"###\s*Edge cases", assistant_text or "", re.I)
    if not match:
        return []
    body = section_body_after_heading(assistant_text, match)
    next_heading = re.search(r"\n###\s+|\n##\s+", body)
    if next_heading:
        body = body[: next_heading.start()]
    items = re.findall(r"^\s*(?:[-*]|\d+[\.)])\s+\S", body, re.M)
    if len(items) < 3:
        return ["### Edge cases (≥3 numbered/bulleted items required)"]
    return []


SYNC_TIMEDB_BATTERY_PATH_MARKERS = (
    "sync_timedb",
    "run_sync_timedb_regression_battery",
)

SYNC_TIMEDB_BATTERY_CITATION_RE = re.compile(
    r"day-close-loop-regression-battery|run_sync_timedb_regression_battery",
    re.I,
)

SYNC_TIMEDB_PLAN_MARKERS = (
    "sync_timedb",
    "sync-timedb",
    "day-close",
    "day_close",
    "chunk gate",
    "chunk_gate",
)

SYNC_TIMEDB_PLAN_BATTERY_TODO_RE = re.compile(
    r"id:\s*(?:run-full-battery|regression-battery-script|cross-plan-regression-battery)\b",
    re.I,
)

SYNC_TIMEDB_PLAN_VERIFY_TODO_RE = re.compile(
    r"id:\s*(?:operator-stall-verify-doc|operator-stall-verify|tiered-verify)\b",
    re.I,
)


def paths_trigger_sync_timedb_battery(work_paths: list[str]) -> bool:
    for path in work_paths or ():
        norm = (path or "").replace("\\", "/").lower()
        if any(marker in norm for marker in SYNC_TIMEDB_BATTERY_PATH_MARKERS):
            return True
    return False


def shell_command_from_tool_part(part: dict) -> str | None:
    if not isinstance(part, dict):
        return None
    tool_name = None
    payload = None
    if part.get("type") == "tool_use":
        tool_name = part.get("name")
        payload = part.get("input") or {}
    elif part.get("type") == "tool_call":
        tool_name = part.get("tool_name") or part.get("name")
        payload = part.get("input") or part.get("arguments") or {}
    if tool_name != "Shell" or not isinstance(payload, dict):
        return None
    command = payload.get("command")
    return str(command) if command else None


def transcript_ran_sync_timedb_battery(transcript_rows: list[dict]) -> bool:
    for row in transcript_rows:
        message = row.get("message") or {}
        for part in message.get("content") or []:
            command = shell_command_from_tool_part(part)
            if command and SYNC_TIMEDB_BATTERY_CITATION_RE.search(command):
                return True
    return False


def sync_timedb_regression_battery_issues(
    assistant_text: str,
    transcript_rows: list[dict],
    work_paths: list[str],
) -> list[str]:
    if not paths_trigger_sync_timedb_battery(work_paths):
        return []
    text = assistant_text or ""
    if SYNC_TIMEDB_BATTERY_CITATION_RE.search(text):
        return []
    if transcript_ran_sync_timedb_battery(transcript_rows):
        return []
    return [
        "sync_timedb regression battery: cite test_runs/day-close-loop-regression-battery-*.log "
        "or run tests/run_sync_timedb_regression_battery.sh "
        "(sync-timedb-change-regression-gate.mdc)",
    ]


def plan_touches_sync_timedb(plan_markdown: str) -> bool:
    lower = (plan_markdown or "").lower()
    return any(marker in lower for marker in SYNC_TIMEDB_PLAN_MARKERS)


def sync_timedb_plan_todo_issues(plan_markdown: str) -> list[str]:
    if not plan_touches_sync_timedb(plan_markdown):
        return []
    issues: list[str] = []
    if not SYNC_TIMEDB_PLAN_BATTERY_TODO_RE.search(plan_markdown):
        issues.append(
            "sync_timedb plan YAML must include run-full-battery or "
            "regression-battery-script todo (sync-timedb-change-regression-gate.mdc)",
        )
    if not SYNC_TIMEDB_PLAN_VERIFY_TODO_RE.search(plan_markdown):
        issues.append(
            "sync_timedb plan YAML must include operator-stall-verify-doc todo "
            "(OPERATOR_SYNC_TIMEDB_STALL_VERIFY.md; sync-timedb-change-regression-gate.mdc)",
        )
    return issues


def rule_dual_registration_issues(work_paths: list[str]) -> list[str]:
    issues: list[str] = []
    seen: set[str] = set()
    for path in work_paths:
        needs_entry, basename = rule_file_needs_router_entry(path)
        if not needs_entry or basename in seen:
            continue
        path_obj = Path(path)
        if not path_obj.is_file() and not cursor_rule_file_exists(basename):
            continue
        seen.add(basename)
        router = find_router_file([], rule_path=path)
        if router is None:
            issues.append(
                f"Rule dual registration: `{basename}` — agent-discipline-core.mdc not found",
            )
            continue
        router_text = router.read_text(encoding="utf-8", errors="replace")
        if not rule_listed_in_router(router_text, basename):
            issues.append(
                f"Rule dual registration: `{basename}` missing from "
                "agent-discipline-core.mdc task router",
            )
        hook_router = find_hook_task_router_file()
        if hook_router is None:
            issues.append(
                f"Rule dual registration: `{basename}` — hook_task_router.py not found",
            )
            continue
        hook_text = hook_router.read_text(encoding="utf-8", errors="replace")
        if not rule_listed_in_router(hook_text, basename):
            issues.append(
                f"Rule dual registration: `{basename}` missing from "
                "hook_task_router.py (MONITOR_ROUTER_ENTRIES or HPCPERFSTATS_ROUTER_ENTRIES)",
            )
    return issues


def plan_authority_content_issues(
    transcript_rows: list[dict],
    workspace_roots: Iterable[str] | None = None,
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> list[str]:
    """PLAN_TEMPLATE + operator-discovery gaps in the live disk plan."""
    if not turn_had_live_plan_disk_write_union(
        transcript_rows, transcript_path, full_rows,
    ):
        return []
    plan_markdown = extract_plan_authority_markdown(
        transcript_rows,
        workspace_roots,
        transcript_path=transcript_path,
        full_rows=full_rows,
    )
    return [
        f"Plan disk content missing: {label}"
        for label in plan_content_issues(plan_markdown)
    ]


def close_gate_issues(
    *,
    assistant_text: str,
    transcript_rows: list[dict],
    workspace_roots: Iterable[str] | None = None,
    transcript_path: str | None = None,
    full_rows: list[dict] | None = None,
) -> list[str]:
    full = full_rows if full_rows is not None else transcript_rows
    extra_reads = (
        ledger_read_basenames_this_turn(transcript_path, full)
        if transcript_path
        else set()
    )
    extra_template = (
        ledger_has_template_read_this_turn(transcript_path, full)
        if transcript_path
        else False
    )
    issues = missing_close_gate_sections(assistant_text)
    work_paths = extract_work_paths(transcript_rows, workspace_roots)
    issues.extend(triggered_rule_dispatch_issues(assistant_text, work_paths))
    required_rules = domain_rules_required(assistant_text, work_paths)
    issues.extend(
        domain_rule_read_issues(
            required_rules,
            transcript_rows,
            extra_read_basenames=extra_reads,
        ),
    )
    issues.extend(rule_dual_registration_issues(work_paths))
    issues.extend(edge_cases_issues(assistant_text))
    issues.extend(
        sync_timedb_regression_battery_issues(
            assistant_text,
            transcript_rows,
            work_paths,
        ),
    )
    if turn_had_create_plan_union(transcript_rows, transcript_path, full):
        issues.extend(
            plan_authority_content_issues(
                transcript_rows,
                workspace_roots,
                transcript_path=transcript_path,
                full_rows=full,
            ),
        )
        issues.extend(
            plan_template_read_issues(
                transcript_rows,
                extra_has_template=extra_template,
            ),
        )
        issues.extend(
            plan_disk_sync_issues(transcript_rows, transcript_path, full),
        )
        plan_required = plan_authoring_required_mdc_rules(work_paths)
        issues.extend(
            domain_rule_read_issues(
                plan_required,
                transcript_rows,
                extra_read_basenames=extra_reads,
            ),
        )
    return issues


def missing_close_gate_sections(text: str) -> list[str]:
    missing = []
    source = text or ""
    for pattern, label in CLOSE_GATE_SECTIONS:
        match = pattern.search(source)
        if not match:
            missing.append(label)
            continue
        if label == AGENT_RULE_DISPATCH_LABEL:
            body = section_body_after_heading(source, match)
            if not agent_rule_dispatch_body_ok(body):
                missing.append(AGENT_RULE_DISPATCH_DETAIL_LABEL)
    return missing


def looks_like_implementation_close(text: str, had_edits: bool) -> bool:
    if had_edits and COMPLETION_RE.search(text or ""):
        return True
    if had_edits and re.search(r"\b(approve for merge|merge blocker|self-review)\b", text, re.I):
        return True
    return False


def looks_like_plan_close(text: str, had_plan: bool) -> bool:
    if not had_plan:
        return False
    if COMPLETION_RE.search(text or ""):
        return True
    if PLAN_CLOSE_RE.search(text or ""):
        return True
    if re.search(r"##\s*Agent rule dispatch", text or "", re.I):
        return True
    return False


def looks_like_task_close(text: str, *, had_edits: bool, had_plan: bool) -> bool:
    if looks_like_implementation_close(text, had_edits):
        return True
    return looks_like_plan_close(text, had_plan)


def rule_file_needs_router_entry(file_path: str) -> tuple[bool, str]:
    path = Path(file_path)
    if path.suffix != ".mdc":
        return False, ""
    if "cursor-rules" not in path.parts:
        return False, ""
    if path.name in ROUTER_BASENAMES:
        return False, ""
    return True, path.name


def rule_listed_in_router(router_text: str, rule_basename: str) -> bool:
    return rule_basename in (router_text or "")
