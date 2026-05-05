"""Tests for GET /api/live/jobs/ (Redis-backed live utilization)."""

from unittest.mock import MagicMock, patch

import pytest
from django.test import RequestFactory

from hpcperfstats.site.machine import api


@pytest.mark.django_db
def test_live_jobs_returns_rows_and_decodes_bytes():
  factory = RequestFactory()
  request = factory.get("/api/live/jobs/")
  request.session = {"access_token": "x", "username": "u"}

  fake_redis = MagicMock()
  fake_redis.smembers.return_value = {b"live_job:1:h.example.com"}
  fake_redis.pipeline.return_value = fake_pipe = MagicMock()
  fake_pipe.execute.return_value = [
      {
          b"jid": b"1",
          b"host": b"h.example.com",
          b"cpu_util": b"10.5",
          b"mem_util": b"20",
          b"updated_ts": b"1000",
      },
      3600,
  ]

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api, "_get_live_job_redis_client", return_value=fake_redis
  ):
    response = api.live_jobs(request)

  assert response.status_code == 200
  assert len(response.data["results"]) == 1
  row = response.data["results"][0]
  assert row["jid"] == "1"
  assert row["host"] == "h.example.com"
  assert row["cpu_util"] == 10.5
  assert row["mem_util"] == 20.0
  assert row["updated_ts"] == 1000


@pytest.mark.django_db
def test_live_jobs_drops_stale_index_members():
  factory = RequestFactory()
  request = factory.get("/api/live/jobs/")
  request.session = {"access_token": "x", "username": "u"}

  fake_redis = MagicMock()
  fake_redis.smembers.return_value = {"live_job:9:gone.example.com"}
  fake_redis.pipeline.return_value = fake_pipe = MagicMock()
  fake_pipe.execute.return_value = [{}, -2]

  with patch.object(api, "_require_auth", return_value=None), patch.object(
      api, "_get_live_job_redis_client", return_value=fake_redis
  ):
    response = api.live_jobs(request)

  assert response.status_code == 200
  assert response.data["results"] == []
  fake_redis.srem.assert_called_once()
