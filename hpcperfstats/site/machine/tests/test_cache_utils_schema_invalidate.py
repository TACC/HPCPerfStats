"""invalidate_jid_derived_cache_keys clears persisted job schema snapshot."""
from unittest.mock import MagicMock, patch

import pytest

from hpcperfstats.site.machine.cache_utils import invalidate_jid_derived_cache_keys

pytestmark = pytest.mark.django_db(databases=[])


def test_invalidate_jid_derived_clears_host_data_schema_json():
  with patch(
      "hpcperfstats.site.machine.models.job_data.objects.filter",
  ) as mock_filter, patch(
      "hpcperfstats.site.machine.cache_utils.invalidate_jid_host_window_row_count_cache",
  ):
    qs = MagicMock()
    mock_filter.return_value = qs
    invalidate_jid_derived_cache_keys(["jid-a"])
    mock_filter.assert_called_once()
    qs.update.assert_called_once_with(host_data_schema_json=None)
