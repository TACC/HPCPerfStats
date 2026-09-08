# Precompressed Brotli/Gzip sidecars for hashed static text; on-the-fly fallback
# when a sidecar is missing (Django admin, unhashed vendor). Never zstd here.
# Shared types/buffers/min_length live in nginx-main.conf http.
zstd off;
zstd_static off;
brotli_static on;
gzip_static on;
brotli on;
brotli_comp_level 4;
gzip on;
gzip_comp_level 5;
