# Build recipe for legacy `db` (docker-compose.pg15-primary.yaml): the Hub
# PG15 image as-is, so `podman-compose build db` on a PG15 site cannot bake
# PG18 under the PG15 tag.
FROM docker.io/timescale/timescaledb:2.28.3-pg15
