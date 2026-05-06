"""Crawlable `/pub/` URL prefixes emitted by ``robots_txt``.

When adding a user-facing React route under the public basename, append the
browser path here (no hostname) and extend ``test_web_pages_*`` robots checks.
"""

PUBLIC_ROBOTS_ALLOW_PREFIXES: tuple[str, ...] = (
    "/pub/",
    "/pub/monthly-metrics",
)
