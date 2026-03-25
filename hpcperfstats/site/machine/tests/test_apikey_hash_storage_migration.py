import hashlib
import importlib


def _sha256_hex(raw_value: str) -> str:
  return hashlib.sha256(raw_value.encode("utf-8")).hexdigest()


def test_migration_hashes_apikeys_without_updating_pk_fields():
  # Import via importlib because the module filename starts with digits.
  migration = importlib.import_module(
      "hpcperfstats.site.machine.migrations.0016_apikey_hash_storage"
  )

  class _FakeApiKeyRow:
    def __init__(self, key: str, key_prefix: str = ""):
      self.key = key
      self.key_prefix = key_prefix
      self.save_called = False
      self.update_fields_seen = "unset"

    def save(self, update_fields=None, **kwargs):
      self.save_called = True
      self.update_fields_seen = update_fields

  raw1 = "raw-key-1-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
  fake1 = _FakeApiKeyRow(raw1, key_prefix="")

  raw2 = "raw-key-2-yyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"
  fake2 = _FakeApiKeyRow(raw2, key_prefix="already-set")

  class _FakeQuerySet:
    def __init__(self, rows):
      self._rows = rows

    def iterator(self):
      return iter(self._rows)

  class _FakeManager:
    def __init__(self, rows):
      self._qs = _FakeQuerySet(rows)

    def all(self):
      return self._qs

  class _FakeApiKeyModel:
    objects = _FakeManager([fake1, fake2])

  class _FakeApps:
    def get_model(self, app_label: str, model_name: str):
      assert app_label == "machine"
      assert model_name == "ApiKey"
      return _FakeApiKeyModel

  migration.migrate_apikeys_to_hashed(_FakeApps(), schema_editor=None)

  assert fake1.save_called is True
  assert fake1.update_fields_seen is None
  assert fake1.key_prefix == raw1[:12]
  assert fake1.key == _sha256_hex(raw1)

  # `key_prefix` already populated => skip row (avoid double-hashing).
  assert fake2.save_called is False
  assert fake2.key_prefix == "already-set"
  assert fake2.key == raw2

