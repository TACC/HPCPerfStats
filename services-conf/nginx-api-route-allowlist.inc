# Generated from EXPECTED_ROUTE_TEMPLATES api/* (urlconf_route_catalog).
# Regenerate with write_nginx_api_route_allowlist_include.py; do not hand-edit.
# Exact (=) or anchored regex (~) per route: scanner probes under a real
# route prefix (/api/session/new) 404 here instead of reaching Django.

location = /api/admin_monitor/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/cache/invalidate-page/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/home/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/host_plot/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/job_monitor/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/job_monitor/gpu/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/jobs/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/jobs/filter_options/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/jobs/histograms/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/jobs/histograms/batch/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/pub/cluster-dashboard/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/sacct/ingest/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/schema/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/session/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/session/drop-staff/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/test-login/user/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/user-api-key/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location = /api/user-api-key/rotate/ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location ~ ^/api/jobs/[^/]+/$ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location ~ ^/api/jobs/[^/]+/[^/]+/$ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

location ~ ^/api/jobs/[^/]+/plots/$ {
    include /etc/nginx/nginx-django-proxy-common.inc;
    include /etc/nginx/nginx-csp-no-active.inc;
    proxy_pass http://django_server;
}

