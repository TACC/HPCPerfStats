# No-active-content CSP for redirects, JSON, empty bodies, and non-document responses.
add_header Content-Security-Policy "default-src 'none'; base-uri 'none'; object-src 'none'; frame-ancestors 'self'; form-action 'none'; script-src 'none'; style-src 'none'; img-src 'none'; font-src 'none'; connect-src 'none'; upgrade-insecure-requests; report-uri /csp-report/;" always;
