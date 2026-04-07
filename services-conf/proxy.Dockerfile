FROM alpine:3.22

RUN apk add --no-cache \
    nginx \
    nginx-mod-http-brotli \
    netcat-openbsd

STOPSIGNAL SIGTERM

CMD ["nginx", "-g", "daemon off;"]
