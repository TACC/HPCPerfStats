# On-the-fly compression for Gunicorn JSON/HTML and SPA shells.
# Heal rewrites machine/pub HTML, so those paths must not use brotli_static sidecars.
# Shared types/buffers/min_length live in nginx-main.conf http.
zstd on;
zstd_comp_level 3;
zstd_static off;
brotli on;
brotli_comp_level 4;
brotli_static off;
brotli_window 512k;
gzip on;
gzip_comp_level 5;
gzip_proxied any;
gzip_static off;
