import pytest

from hpcperfstats.analysis.metrics.lib.columnar.testing import (
  table_from_records,
)
from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot


def test_summaryplot_raises_when_no_metric_data():
  jt = table_from_records([], jid="0")
  sp = SummaryPlot(jt)

  with pytest.raises(ValueError) as excinfo:
    sp.plot()

  assert MSG_NO_METRIC_DATA in str(excinfo.value)
