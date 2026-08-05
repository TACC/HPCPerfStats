# Included inside each location that proxies to Gunicorn (see nginx-static-files.conf).
# Nginx is the public security-header authority: hide duplicate upstream values, then
# re-emit the canonical edge headers. Each location adds the matching CSP include
# (no-active for JSON/API, django-html for login/oauth HTML). Do not hide CORS,
# cookies, or application-selected Cache-Control.
proxy_redirect off;
proxy_http_version 1.1;
proxy_set_header Connection "";
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Forwarded-Host nginx;
proxy_set_header X-Forwarded-Port $server_port;
proxy_buffer_size 16k;
proxy_buffers 8 16k;
proxy_busy_buffers_size 32k;
proxy_connect_timeout 120;
proxy_send_timeout 120;
proxy_read_timeout 120;
send_timeout 120;

proxy_hide_header Strict-Transport-Security;
proxy_hide_header Content-Security-Policy;
proxy_hide_header Content-Security-Policy-Report-Only;
proxy_hide_header X-Frame-Options;
proxy_hide_header X-Content-Type-Options;
proxy_hide_header Referrer-Policy;
proxy_hide_header Cross-Origin-Opener-Policy;
proxy_hide_header Permissions-Policy;

include /etc/nginx/nginx-edge-security-headers.inc;
