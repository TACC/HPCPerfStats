from hpcperfstats.tests.public_robots_js_registry import (
    format_public_robots_txt_body,
    load_public_robots_allow_prefixes,
)


def test_public_robots_js_registry_load_and_format():
  prefixes = load_public_robots_allow_prefixes()
  body = format_public_robots_txt_body(prefixes)
  assert body.split("\n")[0] == "User-agent: *"
  assert body.rstrip("\n").endswith("Disallow: /")
  for p in prefixes:
    assert "Allow: {}".format(p) in body
