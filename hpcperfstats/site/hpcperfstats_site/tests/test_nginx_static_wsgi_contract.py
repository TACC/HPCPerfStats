"""Regression: WSGI must not answer STATIC_URL; nginx serves /static/ in production."""

from pathlib import Path

from django.test import Client
from django.urls import reverse


_REPO_ROOT = Path(__file__).resolve().parents[4]
_SERVICES = _REPO_ROOT / "services-conf"

_EDGE_HEADER_MARKERS = (
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Cross-Origin-Opener-Policy",
    "Permissions-Policy",
    "X-Frame-Options",
)

_UPSTREAM_HIDE_HEADERS = (
    "Strict-Transport-Security",
    "Content-Security-Policy",
    "Content-Security-Policy-Report-Only",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
    "Cross-Origin-Opener-Policy",
    "Permissions-Policy",
)


def test_wsgi_client_does_not_serve_static_url_prefix():
  """If Django adds static() routes or static middleware, this contract breaks."""
  client = Client()
  response = client.get("/static/frontend/__nginx_only_wsgi_contract__.txt")
  assert response.status_code == 404


def test_wsgi_client_does_not_serve_spa_shell_routes():
  """The SPA shell is nginx-owned; Django WSGI must not answer /machine/*."""
  client = Client()
  assert client.get("/machine/").status_code == 404
  assert client.get("/machine/jobs/").status_code == 404
  assert client.get("/pub/").status_code == 404


def test_wsgi_resolves_known_app_route():
  """Sanity: Client reaches urlpatterns (avoid false pass on generic 404)."""
  client = Client()
  response = client.get(reverse("csp_report"))
  assert response.status_code == 405


def test_wsgi_robots_txt_is_owned_by_nginx_not_wsgi():
  """Production serves /robots.txt from static files at the edge; Gunicorn must not answer it."""
  client = Client()
  assert client.get("/robots.txt").status_code == 404


def test_nginx_edge_security_headers_inc_covers_transport_and_framing():
  edge = (_SERVICES / "nginx-edge-security-headers.inc").read_text(encoding="utf-8")
  for marker in _EDGE_HEADER_MARKERS:
    assert marker in edge
  assert "max-age=31536000" in edge
  assert "includeSubDomains" in edge
  hsts_lines = [ln for ln in edge.splitlines() if "Strict-Transport-Security" in ln]
  assert hsts_lines, "missing Strict-Transport-Security add_header"
  assert "preload" not in hsts_lines[0].lower()
  assert "SAMEORIGIN" in edge
  assert "frame-ancestors" not in edge  # framing CSP lives in dedicated CSP includes


def test_nginx_static_files_conf_robots_txt_is_static_with_edge_headers():
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location = /robots.txt" in conf
  assert "alias /srv/static/frontend/robots.txt" in conf
  assert "include /etc/nginx/nginx-edge-security-headers.inc" in conf


def test_nginx_static_files_conf_includes_edge_headers_on_every_owned_location():
  """Every nginx-owned location that can emit a body/status must carry edge headers."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  for location in (
      "location = /favicon.ico",
      "location /static/",
      "location /static/frontend/_next/",
      "location /media/",
      "location = /machine",
      "location ^~ /machine/",
      "location = /pub",
      "location ^~ /pub/",
      "location = /robots.txt",
      "location / {",
  ):
    assert location in conf
  # Edge headers must appear at least once per owned location family (include count).
  assert conf.count("include /etc/nginx/nginx-edge-security-headers.inc") >= 9


def test_nginx_static_files_conf_spa_uses_document_csp_not_nginx_hash_header():
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  # Hash CSP is embedded in HTML meta; nginx must not send a competing header.
  assert "include /etc/nginx/nginx-csp-machine.inc" not in conf
  assert "include /etc/nginx/nginx-csp-pub.inc" not in conf
  assert "include /srv/static/frontend/nginx-csp-machine.inc" not in conf
  assert "include /srv/static/frontend/nginx-csp-pub.inc" not in conf
  assert "location ^~ /machine/" in conf
  assert "include /etc/nginx/nginx-edge-security-headers.inc" in conf
  assert "include /etc/nginx/nginx-csp-no-active.inc" in conf


def test_nginx_static_files_conf_denies_non_web_static_suffixes():
  """Config/docs/source-map leftovers and direct sidecar URLs under /static/ must 404."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  deny = r"location ~* ^/static/.*\.(inc|md|markdown|map|example|sh|py|toml|ini|ya?ml|br|gz|zst)$"
  assert deny in conf
  deny_idx = conf.index(deny)
  assert "return 404" in conf[deny_idx : deny_idx + 400]
  assert "location /static/frontend/_next/" in conf
  assert "location ^~ /static/frontend/_next/" not in conf
  next_loc = conf.split("location /static/frontend/_next/")[1].split("location ")[0]
  assert "expires 1y;" in next_loc
  assert "max-age=31536000" in next_loc
  static_loc = conf.split("location /static/")[1].split("location ")[0]
  assert "expires 30d;" in static_loc
  assert "max-age=2592000" in static_loc


def test_proxy_entrypoint_writes_csp_only_under_etc_nginx():
  """CSP includes are regenerated into /etc/nginx from HTML; never under /srv/static."""
  entry = (_SERVICES / "proxy_entrypoint.sh").read_text(encoding="utf-8")
  assert "write_nginx_spa_csp_includes.py" in entry
  assert '--out-dir "${CSP_OUT_DIR}"' in entry
  assert 'CSP_OUT_DIR="${HPCPERFSTATS_PROXY_CSP_OUT_DIR:-/etc/nginx}"' in entry
  assert 'CSP_MACHINE="${CSP_OUT_DIR}/nginx-csp-machine.inc"' in entry
  assert "refusing CSP include under public static tree" in entry
  assert "--out-dir \"${FRONTEND_STATIC_ROOT}\"" not in entry
  regen_idx = entry.index("write_nginx_spa_csp_includes.py")
  validate_idx = entry.index('validate_csp_include "${CSP_PUB}" "pub"')
  nginx_t_idx = entry.index("nginx -t")
  assert regen_idx < validate_idx < nginx_t_idx
  # Bokeh style-src 'unsafe-inline' is allowed; script-src 'unsafe-inline' is not.
  assert 'script-src[^;]*unsafe-inline' in entry
  assert 'grep -q "unsafe-inline"' not in entry
  assert "script-src unsafe-inline" in entry


def test_proxy_csp_validate_rejects_only_script_src_unsafe_inline(tmp_path):
  """Regression: style-src 'unsafe-inline' must not fail proxy CSP validation."""
  import subprocess

  ok = tmp_path / "ok.inc"
  bad = tmp_path / "bad.inc"
  ok.write_text(
      'add_header Content-Security-Policy "style-src \'self\' \'unsafe-inline\'; '
      "script-src 'self' 'sha256-abc=';\" always;\n",
      encoding="utf-8",
  )
  bad.write_text(
      'add_header Content-Security-Policy "script-src \'self\' \'unsafe-inline\';" '
      "always;\n",
      encoding="utf-8",
  )
  # Same predicate as proxy_entrypoint.sh validate_csp_include.
  ok_proc = subprocess.run(
      ["grep", "-E", "script-src[^;]*unsafe-inline", str(ok)],
      check=False,
      capture_output=True,
  )
  bad_proc = subprocess.run(
      ["grep", "-E", "script-src[^;]*unsafe-inline", str(bad)],
      check=False,
      capture_output=True,
  )
  assert ok_proc.returncode != 0, "style-only unsafe-inline must pass validation"
  assert bad_proc.returncode == 0, "script-src unsafe-inline must be detected"


def test_package_frontend_static_has_no_nginx_config_files():
  """Public package static/frontend must not ship nginx CSP includes."""
  frontend = Path(__file__).resolve().parents[1] / "static" / "frontend"
  if not frontend.is_dir():
    return
  leaked = sorted(frontend.rglob("*.inc"))
  assert leaked == [], f"nginx config must not live under public static: {leaked}"


def test_nginx_static_files_conf_returns_favicon_at_edge():
  """Production proxy answers /favicon.ico without involving Django (currently 404)."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location = /favicon.ico" in conf
  assert "return 404" in conf


def test_nginx_static_files_conf_sets_html_types_on_spa_locations():
  """SPA shells get explicit HTML typing (not proxied to Django)."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert conf.count("default_type text/html") == 2
  assert conf.count("charset utf-8") == 2


def test_nginx_static_files_conf_allowlists_django_prefixes_and_default_404():
  """Proxy must not forward unknown paths; Django routes are enumerated explicitly."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  proxy_inc = "/etc/nginx/nginx-django-proxy-common.inc"
  assert proxy_inc in conf
  assert "try_files $uri $uri/ /pub/index.html =503" in conf
  assert "try_files $uri $uri/ /machine/index.html =503" in conf
  for needle in (
      "\nlocation = / {\n",
      "location ^~ /api/",
      "location = /robots.txt",
      "location ^~ /csp-report/",
      "location ^~ /api-key/",
      "location ^~ /admin_monitor/",
      "location ^~ /login/",
      "location ^~ /test-login/",
      "location = /login_prompt",
      "location = /logout",
      "location ^~ /logout/",
      "location = /machine/logout",
      "location = /machine/logout/",
      "location ^~ /oauth_callback/",
      "location / {",
      "return 404;",
  ):
    assert needle in conf


def test_nginx_django_proxy_common_hides_upstream_security_headers():
  """Nginx is the public security-header authority; hide duplicate Django headers."""
  common = (_SERVICES / "nginx-django-proxy-common.inc").read_text(encoding="utf-8")
  assert "proxy_pass" not in common
  assert "proxy_set_header Host $host;" in common
  assert 'proxy_set_header Accept-Encoding "";' in common
  for header in _UPSTREAM_HIDE_HEADERS:
    assert f"proxy_hide_header {header};" in common
  assert "include /etc/nginx/nginx-edge-security-headers.inc" in common
  assert "nginx-csp-no-active.inc" not in common
  # Dynamic application headers must still pass through.
  assert "proxy_hide_header Access-Control-Allow-Origin" not in common
  assert "proxy_hide_header Set-Cookie" not in common
  assert "proxy_hide_header Cache-Control" not in common
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  assert "location ^~ /api/" in conf
  assert conf.count("include /etc/nginx/nginx-csp-no-active.inc") >= 5
  assert "include /etc/nginx/nginx-csp-django-html.inc" in conf


def test_nginx_conf_completes_ocsp_stapling_contract():
  conf = (_SERVICES / "nginx.conf").read_text(encoding="utf-8")
  assert "ssl_stapling on;" in conf
  assert "ssl_stapling_verify on;" in conf
  assert "ssl_trusted_certificate" in conf
  assert "include /etc/nginx/nginx-resolver.inc;" in conf


def test_nginx_main_conf_http_tunables_and_rejects_hardcoded_workers():
  main = (_SERVICES / "nginx-main.conf").read_text(encoding="utf-8")
  assert "worker_processes auto;" in main
  assert "worker_cpu_affinity auto;" in main
  assert "sendfile on;" in main
  assert "tcp_nopush on;" in main
  assert "tcp_nodelay on;" in main
  assert "open_file_cache max=10000" in main
  assert "pcre_jit on;" in main
  assert "ssl_session_tickets off;" in main
  assert "worker_processes 20" not in main
  assert "api;" not in main
  assert "client_body_early_read" not in main


def test_nginx_vhost_enables_http2_and_ssl_session_cache():
  conf = (_SERVICES / "nginx.conf").read_text(encoding="utf-8")
  assert "http2 on;" in conf
  assert "ssl_session_cache shared:SSL:10m;" in conf
  assert "ssl_session_timeout 1d;" in conf
  assert "api;" not in conf
  assert "client_body_early_read" not in conf


def test_spa_open_file_cache_off_on_machine_and_pub():
  """SPA heal replaces index.html; locations must not reuse cached fds."""
  conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  machine = conf.split("location ^~ /machine/")[1].split("location ")[0]
  pub = conf.split("location ^~ /pub/")[1].split("location ")[0]
  assert "open_file_cache off;" in machine
  assert "open_file_cache off;" in pub


def test_proxy_dockerfile_wires_ocsp_trust_and_startup_helpers():
  dockerfile = (_SERVICES / "proxy.Dockerfile").read_text(encoding="utf-8")
  assert "ca-certificates" in dockerfile
  assert "write_nginx_resolver_include.py" in dockerfile
  assert "proxy_entrypoint.sh" in dockerfile
  assert 'CMD ["/usr/local/bin/proxy_entrypoint.sh"]' in dockerfile or (
      "ENTRYPOINT" in dockerfile and "proxy_entrypoint" in dockerfile
  )
  # Shared snippets are compose bind-mounts only — do not also COPY them into the image.
  for mount_only in (
      "nginx-edge-security-headers.inc",
      "nginx-csp-no-active.inc",
      "nginx-csp-django-html.inc",
      "nginx-static-files.conf",
      "nginx-django-proxy-common.inc",
      "nginx-compress-proxy.inc",
      "nginx-compress-static.inc",
  ):
    assert f"COPY services-conf/{mount_only}" not in dockerfile


def test_proxy_entrypoint_sh_is_tracked_not_gitignored():
  """Regression: *.sh gitignore must not hide services-conf/proxy_entrypoint.sh."""
  import subprocess

  repo_root = _SERVICES.parent
  ignored = subprocess.run(
      ["git", "check-ignore", "-v", "services-conf/proxy_entrypoint.sh"],
      cwd=repo_root,
      check=False,
      capture_output=True,
      text=True,
  )
  assert ignored.returncode != 0, ignored.stdout + ignored.stderr
  assert (_SERVICES / "proxy_entrypoint.sh").is_file()
  assert (_SERVICES / "proxy_entrypoint.sh").stat().st_mode & 0o111


def test_nginx_hybrid_compression_location_split():
  """Zstd on proxy/SPA; Brotli/Gzip sidecars on /static/; no CORS wildcard."""
  main = (_SERVICES / "nginx-main.conf").read_text(encoding="utf-8")
  vhost = (_SERVICES / "nginx.conf").read_text(encoding="utf-8")
  static_conf = (_SERVICES / "nginx-static-files.conf").read_text(encoding="utf-8")
  common = (_SERVICES / "nginx-django-proxy-common.inc").read_text(encoding="utf-8")
  proxy_inc = (_SERVICES / "nginx-compress-proxy.inc").read_text(encoding="utf-8")
  static_inc = (_SERVICES / "nginx-compress-static.inc").read_text(encoding="utf-8")
  assert "gzip off;" in main
  assert "brotli off;" in main
  assert "zstd off;" in main
  assert "gzip_min_length 256;" in main
  assert "brotli_min_length 256;" in main
  assert "zstd_min_length 256;" in main
  assert "gzip_vary on;" in main
  assert "gzip on;" not in vhost
  assert "brotli on;" not in vhost
  assert "zstd on;" in proxy_inc
  assert "zstd_comp_level 3;" in proxy_inc
  assert "brotli_comp_level 4;" in proxy_inc
  assert "brotli_static off;" in proxy_inc
  assert "gzip_static off;" in proxy_inc
  assert "zstd off;" in static_inc
  assert "brotli_static on;" in static_inc
  assert "gzip_static on;" in static_inc
  assert "include /etc/nginx/nginx-compress-proxy.inc;" in common
  machine = static_conf.split("location ^~ /machine/")[1].split("location ")[0]
  pub = static_conf.split("location ^~ /pub/")[1].split("location ")[0]
  assert "include /etc/nginx/nginx-compress-proxy.inc;" in machine
  assert "include /etc/nginx/nginx-compress-proxy.inc;" in pub
  assert "include /etc/nginx/nginx-compress-static.inc;" in static_conf
  assert "Access-Control-Allow-Origin *" not in static_conf
  assert "Access-Control-Allow-Origin *" not in static_inc
  assert "Access-Control-Allow-Origin *" not in proxy_inc
  media = static_conf.split("location /media/")[1].split("location ")[0]
  assert "nginx-compress-" not in media


def test_nginx_csp_no_active_inc_forbids_scripts_and_styles():
  csp = (_SERVICES / "nginx-csp-no-active.inc").read_text(encoding="utf-8")
  assert "Content-Security-Policy" in csp
  assert "script-src 'none'" in csp
  assert "style-src 'none'" in csp
  assert "unsafe-inline" not in csp
  assert "unsafe-eval" not in csp
