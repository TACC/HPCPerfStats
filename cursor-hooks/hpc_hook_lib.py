"""Shared helpers for HPCPerfStats Cursor hooks (stdlib only)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

EDIT_TOOL_NAMES = frozenset(
    {"Write", "StrReplace", "EditNotebook", "Delete", "ApplyPatch"},
)

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


def edit_payload_from_tool_part(part: dict) -> dict | None:
    if not isinstance(part, dict):
        return None
    tool_name = tool_name_from_tool_part(part)
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
) -> str:
    """Authoritative plan body for hook validation — disk file, not CreatePlan tool."""
    for plan_path in reversed(live_plan_disk_paths_from_rows(rows)):
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


def plan_disk_sync_issues(transcript_rows: list[dict]) -> list[str]:
    """CreatePlan or plan-close turns must also Write/StrReplace a .cursor/plans/*.plan.md file."""
    had_create_plan = turn_had_create_plan(transcript_rows)
    had_disk = turn_had_live_plan_disk_write(transcript_rows)
    if had_create_plan and not had_disk:
        return [
            "Plan not written to disk: Write or StrReplace "
            "<workspace>/.cursor/plans/*.plan.md required; CreatePlan/chat alone does not count "
            "(plan-live-disk-sync.mdc)",
        ]
    return []


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


def _validate_pending_commands_subsection(pending: str) -> list[str]:
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
    for section in service_sections:
        label_match = re.match(r"####\s*(\S+)", section)
        if not label_match:
            continue
        service = label_match.group(1)
        if not re.search(r"```bash\b", section, re.I):
            issues.append(
                f"Operator discovery: #### {service} missing fenced bash block",
            )
            continue
        bash_match = re.search(r"```bash\s*\n(.*?)```", section, re.S | re.I)
        if bash_match and not re.search(
            r"docker\s+compose\s+(exec|run|logs)",
            bash_match.group(1),
            re.I,
        ):
            issues.append(
                f"Operator discovery: #### {service} bash block must use "
                "docker compose exec|run|logs",
            )
    return issues


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


def turn_create_plan_pending_disk_write(rows: list[dict]) -> bool:
    """True when CreatePlan ran this turn but no live plan disk write followed it."""
    seq = 0
    last_create_seq: int | None = None
    disk_write_after_create = False
    for row in rows:
        if row.get("role") != "assistant":
            continue
        for part in (row.get("message") or {}).get("content") or []:
            if not isinstance(part, dict):
                continue
            if is_create_plan_tool_part(part):
                last_create_seq = seq
            path = edit_path_from_tool_part(part)
            if (
                path
                and is_live_plan_disk_path(path)
                and last_create_seq is not None
                and seq > last_create_seq
            ):
                disk_write_after_create = True
            seq += 1
    return last_create_seq is not None and not disk_write_after_create


def plan_authoring_precreate_read_issues(transcript_rows: list[dict]) -> list[str]:
    """Required Read tool calls before the first CreatePlan in this turn."""
    issues = domain_rule_read_issues(list(PLAN_AUTHORING_REQUIRED_MDC), transcript_rows)
    issues.extend(plan_template_read_issues(transcript_rows))
    return issues


def is_allowed_tool_while_plan_disk_pending(tool_name: str, tool_input: dict) -> bool:
    if tool_name == "Read":
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


def read_path_from_tool_part(part: dict) -> str | None:
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
    if tool_name != "Read" or not isinstance(payload, dict):
        return None
    path = payload.get("path") or payload.get("file_path") or payload.get("target_file")
    return str(path) if path else None


def is_cursor_rule_read_path(path: str) -> bool:
    normalized = (path or "").replace("\\", "/")
    return (
        "cursor-rules/" in normalized
        and normalized.endswith(".mdc")
        and (
            "monitor/cursor-rules/" in normalized
            or "hpcperfstats/cursor-rules/" in normalized
        )
    )


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


def plan_template_read_issues(transcript_rows: list[dict]) -> list[str]:
    first_closeable = first_closeable_event_index(transcript_rows)
    read_indices = plan_template_read_event_indices(transcript_rows)
    if not read_indices:
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
    return missing


def domain_rule_read_issues(required_rules: list[str], transcript_rows: list[dict]) -> list[str]:
    first_closeable = first_closeable_event_index(transcript_rows)
    issues: list[str] = []
    for rule in sorted(required_rules, key=str.lower):
        if rule.lower() in READ_VERIFY_EXEMPT_MDC:
            continue
        if not cursor_rule_file_exists(rule):
            continue
        read_indices = read_event_indices_for_rule(transcript_rows, rule)
        if not read_indices:
            issues.append(f"Rule not read: {rule}")
        elif first_closeable is not None and min(read_indices) > first_closeable:
            issues.append(f"Rule read after first edit/plan: {rule}")
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
) -> list[str]:
    """PLAN_TEMPLATE + operator-discovery gaps in the live disk plan."""
    if not turn_had_live_plan_disk_write(transcript_rows):
        return []
    plan_markdown = extract_plan_authority_markdown(transcript_rows, workspace_roots)
    return [
        f"Plan disk content missing: {label}"
        for label in plan_content_issues(plan_markdown)
    ]


def close_gate_issues(
    *,
    assistant_text: str,
    transcript_rows: list[dict],
    workspace_roots: Iterable[str] | None = None,
) -> list[str]:
    issues = missing_close_gate_sections(assistant_text)
    work_paths = extract_work_paths(transcript_rows, workspace_roots)
    issues.extend(triggered_rule_dispatch_issues(assistant_text, work_paths))
    required_rules = domain_rules_required(assistant_text, work_paths)
    issues.extend(domain_rule_read_issues(required_rules, transcript_rows))
    issues.extend(rule_dual_registration_issues(work_paths))
    issues.extend(edge_cases_issues(assistant_text))
    if turn_had_create_plan(transcript_rows):
        issues.extend(plan_authority_content_issues(transcript_rows, workspace_roots))
        issues.extend(plan_template_read_issues(transcript_rows))
        issues.extend(plan_disk_sync_issues(transcript_rows))
        plan_required = plan_authoring_required_mdc_rules(work_paths)
        issues.extend(domain_rule_read_issues(plan_required, transcript_rows))
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
