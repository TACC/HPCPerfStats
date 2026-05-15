FROM alpine:3.23

RUN apk add --no-cache \
    nginx-mod-http-brotli \
    netcat-openbsd \
    python3

RUN apk upgrade nginx --repository=https://dl-cdn.alpinelinux.org/alpine/edge/main

RUN nginx -v

RUN mkdir -p /usr/local/lib/hpcperfstats-proxy

COPY services-conf/parse_hpcperfstats_proxy_hosts.py \
    services-conf/write_nginx_proxy_allowed_hosts_include.py \
    /usr/local/lib/hpcperfstats-proxy/

ENV PYTHONPATH=/usr/local/lib/hpcperfstats-proxy

RUN chmod 755 /usr/local/lib/hpcperfstats-proxy/write_nginx_proxy_allowed_hosts_include.py

WORKDIR /build

# Prefer the deployment ini when present in the build context; otherwise fall back to the example.
COPY hpcperfstats.ini* /build/

# Prefer gitignored services-conf/nginx.conf when present; otherwise services-conf/nginx.conf.example.
COPY services-conf/nginx.conf* /build/

RUN set -eu; \
    if [ -f /build/hpcperfstats.ini ]; then INI=/build/hpcperfstats.ini; \
    elif [ -f /build/hpcperfstats.ini.example ]; then INI=/build/hpcperfstats.ini.example; \
    else echo "missing hpcperfstats.ini or hpcperfstats.ini.example in build context"; exit 1; \
    fi; \
    if [ -f /build/nginx.conf ]; then MAIN_CONF=/build/nginx.conf; \
    elif [ -f /build/nginx.conf.example ]; then MAIN_CONF=/build/nginx.conf.example; \
    else echo "missing services-conf/nginx.conf or nginx.conf.example in build context"; exit 1; \
    fi; \
    cp "${MAIN_CONF}" /etc/nginx/http.d/default.conf; \
    python3 /usr/local/lib/hpcperfstats-proxy/write_nginx_proxy_allowed_hosts_include.py \
      --ini "${INI}" \
      --out /etc/nginx/hps-proxy-allowed-hosts.inc; \
    rm -rf /build

STOPSIGNAL SIGTERM

CMD ["nginx", "-g", "daemon off;"]
