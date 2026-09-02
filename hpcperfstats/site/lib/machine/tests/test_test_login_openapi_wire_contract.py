"""Wire-contract tests for session and test-login JSON shapes."""

from __future__ import annotations

import pytest

from hpcperfstats.site.lib.machine.openapi_serializers import (
    SessionInfoSerializer,
    TestLoginUserSerializer,
)

pytestmark = pytest.mark.machine_unit_mock

SESSION_WIRE = {
    "logged_in": True,
    "username": "alice",
    "is_staff": True,
    "machine_name": "cluster.test",
    "separate_test_login": True,
}

TEST_LOGIN_USER_WIRE = {
    "configured": True,
    "username": "qa",
    "login_url": "/test-login/",
}


def test_session_info_openapi_accepts_live_wire():
  serializer = SessionInfoSerializer(data=SESSION_WIRE)
  assert serializer.is_valid(), serializer.errors


def test_test_login_user_openapi_accepts_live_wire():
  serializer = TestLoginUserSerializer(data=TEST_LOGIN_USER_WIRE)
  assert serializer.is_valid(), serializer.errors
  assert "password" not in serializer.validated_data
  assert "password_hash" not in serializer.validated_data
