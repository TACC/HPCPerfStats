import pandas as pd
import pytest

from hpcperfstats.analysis.metrics.lib.plot import MSG_NO_METRIC_DATA
from hpcperfstats.analysis.metrics.lib.plot.summaryplot import SummaryPlot


class _EmptyJidTable:
  jid = 0
  host_list = []

  def get_host_time_df(self):
    return pd.DataFrame()

  def get_aggregate_df(self, *args, **kwargs):
    return pd.DataFrame()


def test_summaryplot_raises_when_no_metric_data():
  jt = _EmptyJidTable()
  sp = SummaryPlot(jt)

  with pytest.raises(ValueError) as excinfo:
    sp.plot()

  assert MSG_NO_METRIC_DATA in str(excinfo.value)

