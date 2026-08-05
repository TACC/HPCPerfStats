# Defense-in-depth CSP for Django HTML pages proxied through nginx (login/oauth).
# SPA shells use build-generated hash policies under /srv/static/frontend/.
add_header Content-Security-Policy "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'self'; form-action 'self'; img-src 'self' data:; font-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; upgrade-insecure-requests; report-uri /csp-report/;" always;
