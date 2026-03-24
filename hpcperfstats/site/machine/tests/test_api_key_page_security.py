import re
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import Client

from hpcperfstats.site.machine.models import ApiKey


class TestApiKeyPageSecurity:
  def test_post_requires_csrf_token(self):
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    with patch("hpcperfstats.site.hpcperfstats_site.views.check_for_tokens", return_value=True):
      response = client.post("/api-key/")

    assert response.status_code == 403

  def test_key_is_shown_only_once_and_stored_hashed(self):
    client = Client(enforce_csrf_checks=True)
    session = client.session
    session["username"] = "alice"
    session["is_staff"] = False
    session.save()

    fake_key = "a" * 64
    fake_obj = SimpleNamespace(key_prefix=fake_key[:12])
    fake_qs = MagicMock()
    fake_qs.order_by.return_value.first.return_value = None

    with patch("hpcperfstats.site.hpcperfstats_site.views.check_for_tokens", return_value=True), patch(
      "hpcperfstats.site.hpcperfstats_site.views.ApiKey.objects.filter", return_value=fake_qs
    ), patch(
      "hpcperfstats.site.hpcperfstats_site.views.ApiKey.create_from_raw_key",
      return_value=(fake_obj, fake_key),
    ):
      response = client.get("/api-key/")

    assert response.status_code == 200
    html = response.content.decode("utf-8")
    assert "This key is shown only once." in html
    match = re.search(r"<p><code>([a-f0-9]{64})</code></p>", html)
    assert match is not None
    raw_key = match.group(1)

    assert ApiKey.hash_raw_key(raw_key) != raw_key

    existing_obj = SimpleNamespace(key_prefix=fake_key[:12])
    existing_qs = MagicMock()
    existing_qs.order_by.return_value.first.return_value = existing_obj
    with patch("hpcperfstats.site.hpcperfstats_site.views.check_for_tokens", return_value=True), patch(
      "hpcperfstats.site.hpcperfstats_site.views.ApiKey.objects.filter", return_value=existing_qs
    ):
      second = client.get("/api-key/")

    second_html = second.content.decode("utf-8")
    assert second.status_code == 200
    assert "cannot be shown again" in second_html
    assert raw_key not in second_html
