"""HTTP query-latency measurement and percentile reports."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any


@dataclass
class ProbeResult:
    """One timed HTTP GET."""

    name: str
    url: str
    status: int | None
    elapsed_ms: float
    bytes_read: int
    error: str | None = None


@dataclass
class ScenarioStats:
    """Percentiles across repeats of one named scenario."""

    name: str
    n: int
    ok: int
    p50_ms: float | None
    p95_ms: float | None
    max_ms: float | None
    statuses: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def substitute_placeholders(text: str, mapping: dict[str, str]) -> str:
    """Replace ``{{KEY}}`` tokens; unknown tokens are left in place."""
    out = text
    for key, value in mapping.items():
        out = out.replace("{{" + key + "}}", value)
    return out


def default_placeholders(
    *,
    jid: str = "",
    username: str = "",
    queue: str = "",
    host: str = "",
    today: date | None = None,
) -> dict[str, str]:
    """Fill date-span placeholders used by query_latency.json."""
    day = today or date.today()
    return {
        "JID": jid,
        "USERNAME": username,
        "QUEUE": queue,
        "HOST": host,
        "END_1D": day.isoformat(),
        "END_7D": (day - timedelta(days=6)).isoformat(),
        "END_30D": (day - timedelta(days=29)).isoformat(),
        "END_365D": (day - timedelta(days=364)).isoformat(),
        "TODAY": day.isoformat(),
        "YEAR": f"{day.year}",
    }


def load_scenarios(path: str, placeholders: dict[str, str]) -> list[dict[str, Any]]:
    """Load named GET scenarios from JSON and substitute placeholders."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    scenarios = payload.get("scenarios", payload)
    out: list[dict[str, Any]] = []
    raw_text = json.dumps(scenarios)
    filled = json.loads(substitute_placeholders(raw_text, placeholders))
    for item in filled:
        out.append(item)
    return out


def build_url(base_url: str, path: str, query: dict[str, str] | None) -> str:
    """Join API base, path, and query string."""
    base = base_url.rstrip("/") + "/"
    rel = path.lstrip("/")
    url = urllib.parse.urljoin(base, rel)
    if query:
        filtered = {k: v for k, v in query.items() if v not in (None, "")}
        if filtered:
            url += "?" + urllib.parse.urlencode(filtered)
    return url


def timed_get(
    url: str,
    *,
    headers: dict[str, str],
    timeout_s: float,
    verify_tls: bool,
) -> ProbeResult:
    """Issue one GET and record wall time, status, and body size."""
    context = None if verify_tls else ssl._create_unverified_context()
    request = urllib.request.Request(url, headers=headers, method="GET")
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout_s, context=context) as resp:
            body = resp.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            return ProbeResult(
                name="",
                url=url,
                status=getattr(resp, "status", None) or resp.getcode(),
                elapsed_ms=elapsed_ms,
                bytes_read=len(body),
            )
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        try:
            payload = exc.read()
            nbytes = len(payload)
        except Exception:
            nbytes = 0
        return ProbeResult(
            name="",
            url=url,
            status=exc.code,
            elapsed_ms=elapsed_ms,
            bytes_read=nbytes,
            error=f"HTTP {exc.code}",
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        return ProbeResult(
            name="",
            url=url,
            status=None,
            elapsed_ms=elapsed_ms,
            bytes_read=0,
            error=str(exc),
        )


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile; ``pct`` is 0–100."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] * (1.0 - frac) + ordered[high] * frac


def summarize_repeats(name: str, probes: list[ProbeResult]) -> ScenarioStats:
    """Compute p50/p95/max over successful 2xx probes."""
    ok_times = [
        p.elapsed_ms for p in probes if p.status is not None and 200 <= p.status < 300
    ]
    errors = [p.error for p in probes if p.error]
    statuses = [p.status for p in probes if p.status is not None]
    return ScenarioStats(
        name=name,
        n=len(probes),
        ok=len(ok_times),
        p50_ms=percentile(ok_times, 50) if ok_times else None,
        p95_ms=percentile(ok_times, 95) if ok_times else None,
        max_ms=max(ok_times) if ok_times else None,
        statuses=statuses,
        errors=[e for e in errors if e],
    )


def stats_to_dict(stats: ScenarioStats) -> dict[str, Any]:
    """JSON-ready scenario summary. Missing timings stay null, not guessed."""
    return {
        "name": stats.name,
        "n": stats.n,
        "ok": stats.ok,
        "p50_ms": None if stats.p50_ms is None else round(stats.p50_ms, 1),
        "p95_ms": None if stats.p95_ms is None else round(stats.p95_ms, 1),
        "max_ms": None if stats.max_ms is None else round(stats.max_ms, 1),
        "statuses": stats.statuses,
        "errors": stats.errors,
        "not_measured": stats.ok == 0,
    }


def run_scenarios(
    scenarios: list[dict[str, Any]],
    *,
    base_url: str,
    api_key: str | None,
    repeats: int,
    warmup: int,
    timeout_s: float,
    verify_tls: bool,
    get_fn=timed_get,
) -> dict[str, Any]:
    """Run named scenarios; warmup probes are discarded from percentiles."""
    headers = {"Accept": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    reports: list[dict[str, Any]] = []
    all_probes: list[dict[str, Any]] = []
    for scenario in scenarios:
        name = str(scenario.get("name") or "unnamed")
        path = str(scenario.get("path") or "")
        query = scenario.get("query") or {}
        skip_if_empty = scenario.get("skip_if_empty") or []
        if "/jobs//" in path:
            reports.append(
                {
                    "name": name,
                    "skipped": True,
                    "reason": "missing job id in path",
                    "not_measured": True,
                }
            )
            continue
        if any(not query.get(key) for key in skip_if_empty):
            reports.append(
                {
                    "name": name,
                    "skipped": True,
                    "reason": f"missing required query keys {skip_if_empty}",
                    "not_measured": True,
                }
            )
            continue
        url = build_url(base_url, path, query)
        probes: list[ProbeResult] = []
        total = warmup + repeats
        for idx in range(total):
            probe = get_fn(
                url,
                headers=headers,
                timeout_s=timeout_s,
                verify_tls=verify_tls,
            )
            probe.name = name
            record = {
                "name": name,
                "url": _redact_url(url),
                "status": probe.status,
                "elapsed_ms": round(probe.elapsed_ms, 2),
                "bytes_read": probe.bytes_read,
                "error": probe.error,
                "warmup": idx < warmup,
            }
            all_probes.append(record)
            if idx >= warmup:
                probes.append(probe)
        reports.append(stats_to_dict(summarize_repeats(name, probes)))
    return {
        "base_url": base_url.rstrip("/"),
        "repeats": repeats,
        "warmup": warmup,
        "scenarios": reports,
        "probes": all_probes,
    }


def _redact_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")
    )
