"""Unit tests for listend_drain queue drain loop."""
from __future__ import annotations

from unittest.mock import MagicMock, patch



def _make_get_sequence(*frames_and_bodies):
  """Build basic_get side_effect: each item is (method_frame_or_none, body)."""
  results = []
  for item in frames_and_bodies:
    if item[0] is None:
      results.append((None, None, None))
    else:
      tag, body = item
      frame = MagicMock(delivery_tag=tag)
      results.append((frame, None, body))
  return results


@patch("hpcperfstats.listend_drain.append_monitor_payload_to_archive")
@patch("hpcperfstats.listend_drain.pika.BlockingConnection")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_queue", return_value="test-q")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_server", return_value="localhost")
def test_drain_queue_empty(_srv, _q, mock_conn_cls, mock_append):
  channel = MagicMock()
  channel.basic_get.return_value = (None, None, None)
  connection = MagicMock()
  connection.is_closed = False
  connection.channel.return_value = channel
  mock_conn_cls.return_value = connection

  from hpcperfstats.listend_drain import drain_queue_to_archive

  assert drain_queue_to_archive() == 0
  channel.basic_ack.assert_not_called()
  mock_append.assert_not_called()


@patch("hpcperfstats.listend_drain.append_monitor_payload_to_archive")
@patch("hpcperfstats.listend_drain.pika.BlockingConnection")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_queue", return_value="test-q")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_server", return_value="localhost")
def test_drain_queue_processes_and_acks(_srv, _q, mock_conn_cls, mock_append):
  channel = MagicMock()
  channel.basic_get.side_effect = _make_get_sequence(
      (1, b"payload1"),
      (2, b"payload2"),
      (None, None),
  )
  connection = MagicMock()
  connection.is_closed = False
  connection.channel.return_value = channel
  mock_conn_cls.return_value = connection

  from hpcperfstats.listend_drain import drain_queue_to_archive

  assert drain_queue_to_archive() == 2
  assert mock_append.call_count == 2
  assert channel.basic_ack.call_count == 2


@patch("hpcperfstats.listend_drain.append_monitor_payload_to_archive")
@patch("hpcperfstats.listend_drain.pika.BlockingConnection")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_queue", return_value="test-q")
@patch("hpcperfstats.listend_drain.cfg.get_rmq_server", return_value="localhost")
def test_drain_queue_nacks_on_processing_error(_srv, _q, mock_conn_cls, mock_append):
  mock_append.side_effect = RuntimeError("bad payload")
  channel = MagicMock()
  channel.basic_get.side_effect = _make_get_sequence(
      (1, b"bad"),
      (None, None),
  )
  connection = MagicMock()
  connection.is_closed = False
  connection.channel.return_value = channel
  mock_conn_cls.return_value = connection

  from hpcperfstats.listend_drain import drain_queue_to_archive

  assert drain_queue_to_archive() == 1
  channel.basic_nack.assert_called_once_with(delivery_tag=1, requeue=True)


@patch("hpcperfstats.dbload.lib.process_title.set_script_process_title")
@patch("hpcperfstats.listend_drain.drain_queue_to_archive")
def test_main_calls_drain(mock_drain, mock_title):
  from hpcperfstats import listend_drain

  listend_drain.main()
  mock_title.assert_called_once()
  mock_drain.assert_called_once()
