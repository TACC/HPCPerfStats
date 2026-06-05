#!/usr/bin/env bash
# Shared docker-compose invocation for test workflows (source, do not execute).
# Usage: . "$(dirname "${BASH_SOURCE[0]}")/compose_test_cmd.sh"
#        compose_test up -d db redis

COMPOSE_TEST=(docker-compose -f docker-compose.yaml -f tests/docker-compose.test-overlay.yaml)

compose_test() {
  "${COMPOSE_TEST[@]}" "$@"
}
