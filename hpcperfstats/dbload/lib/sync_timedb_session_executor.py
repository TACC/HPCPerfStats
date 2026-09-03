"""
Session-static and ephemeral thread pools for sync_timedb background roles.

Attributes:
  R: Worker return type variable.
  T: Input item type variable.
  SessionSingleFlightExecutor: Single-worker background executor.
  SyncTimedbThreadPool: Titled in-process worker pool.
  ThreadPoolAsyncResult: Future wrapper with ready/get.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Iterator, Optional, Tuple, TypeVar

from django.db import close_old_connections

from hpcperfstats.dbload.lib.process_title import set_daemon_thread_title

T = TypeVar("T")
R = TypeVar("R")


class ThreadPoolAsyncResult:
  """
  multiprocessing.Pool AsyncResult stand-in backed by a Future.

  Attributes:
    _future: Executor future for the submitted job.
  """

  def __init__(self, future: Future[Any]) -> None:
    """
    Wrap one executor future.

    Args:
      future (Future[Any]): Future returned by ThreadPoolExecutor.submit.

    Returns:
      None

    Examples:
      >>> ThreadPoolAsyncResult(Future()).ready()
      False
    """
    self._future = future

  def ready(self) -> bool:
    """
    Return True when the wrapped future has finished.

    Returns:
      bool: True when done.

    Examples:
      >>> ThreadPoolAsyncResult(Future()).ready()
      False
    """
    return self._future.done()

  def successful(self) -> bool:
    """
    Return True when the future finished without raising.

    Returns:
      bool: True when done and no exception was stored.

    Examples:
      >>> ThreadPoolAsyncResult(Future()).successful()
      False
    """
    return self._future.done() and self._future.exception() is None

  def get(self, timeout: float | None = None) -> Any:
    """
    Return the worker result or raise the worker exception.

    Args:
      timeout (float | None): Seconds to wait, or None to block.

    Returns:
      Any: Value returned by the worker.

    Examples:
      >>> ThreadPoolAsyncResult(Future()).get(timeout=0)  # doctest: +SKIP
    """
    return self._future.result(timeout=timeout)


class SyncTimedbThreadPool:
  """
  ThreadPoolExecutor with a multiprocessing.Pool apply_async surface.

  Attributes:
    process_title: Daemon process title used for thread titles.
    thread_role: Role label such as ingest-pool.
    _executor: Underlying thread pool.
    _initargs: Initializer positional arguments.
    _initializer: Optional per-task initializer.
  """

  def __init__(
    self,
    *,
    max_workers: int,
    thread_role: str,
    process_title: str = "sync_timedb.py",
    initializer: Callable[..., Any] | None = None,
    initargs: tuple[Any, ...] = (),
  ) -> None:
    """
    Create a titled thread pool.

    Args:
      max_workers (int): Pool size.
      thread_role (str): Title role such as ingest-pool.
      process_title (str): Script name used in thread titles.
      initializer (Callable[..., Any] | None): Optional per-task setup.
      initargs (tuple[Any, ...]): Arguments for initializer.

    Returns:
      None

    Examples:
      >>> SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
      <...>  # doctest: +SKIP
    """
    self.process_title = str(process_title)
    self.thread_role = str(thread_role)
    self._initializer = initializer
    self._initargs = tuple(initargs)
    self._executor = ThreadPoolExecutor(
        max_workers=max(1, int(max_workers)),
        thread_name_prefix=self.thread_role,
    )

  def apply_async(
    self,
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwds: dict[str, Any] | None = None,
    callback: Callable[[Any], Any] | None = None,
    error_callback: Callable[[BaseException], Any] | None = None,
  ) -> ThreadPoolAsyncResult:
    """
    Submit one job and return an AsyncResult-compatible handle.

    Args:
      fn (Callable[..., Any]): Worker callable.
      args (tuple[Any, ...]): Positional arguments for the worker.
      kwds (dict[str, Any] | None): Keyword arguments for the worker.
      callback (Callable[[Any], Any] | None): Unused Pool-compat argument.
      error_callback (Callable[[BaseException], Any] | None): Unused
        Pool-compat argument.

    Returns:
      ThreadPoolAsyncResult: Handle with ready() and get().

    Examples:
      >>> pool = SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
      >>> pool.apply_async(lambda: 1).get()
      1
      >>> pool.terminate(); pool.join()
    """
    del callback, error_callback
    kwargs = dict(kwds or {})

    def _run() -> Any:
      """
      Title this worker thread, close stale ORM connections, then run fn.

      Returns:
        Any: Worker return value.

      Examples:
        >>> callable(_run)
        True
      """
      set_daemon_thread_title(
          "",
          script_name=self.process_title,
          role=self.thread_role,
      )
      close_old_connections()
      if self._initializer is not None:
        self._initializer(*self._initargs)
      try:
        return fn(*args, **kwargs)
      finally:
        close_old_connections()

    return ThreadPoolAsyncResult(self._executor.submit(_run))

  def close(self) -> None:
    """
    Refuse new work; existing jobs may still finish.

    Returns:
      None

    Examples:
      >>> pool = SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
      >>> pool.close()
    """
    self._executor.shutdown(wait=False, cancel_futures=False)

  def terminate(self) -> None:
    """
    Cancel pending futures and shut the pool down.

    Returns:
      None

    Examples:
      >>> pool = SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
      >>> pool.terminate()
    """
    self._executor.shutdown(wait=False, cancel_futures=True)

  def join(self) -> None:
    """
    Wait for the underlying executor to finish shutting down.

    Returns:
      None

    Examples:
      >>> pool = SyncTimedbThreadPool(max_workers=1, thread_role="ingest-pool")
      >>> pool.terminate(); pool.join()
    """
    self._executor.shutdown(wait=True)

  def __enter__(self) -> "SyncTimedbThreadPool":
    """
    Return this pool for a with-statement.

    Returns:
      SyncTimedbThreadPool: This pool.

    Examples:
      >>> with SyncTimedbThreadPool(max_workers=1, thread_role="x") as pool:
      ...   pool.apply_async(lambda: 2).get()
      2
    """
    return self

  def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
    """
    Shut the pool down when leaving a with-statement.

    Args:
      exc_type (Any): Exception type, or None.
      exc (Any): Exception instance, or None.
      tb (Any): Traceback, or None.

    Returns:
      None

    Examples:
      >>> with SyncTimedbThreadPool(max_workers=1, thread_role="x"):
      ...   pass
    """
    del exc_type, exc, tb
    self.terminate()
    self.join()


def create_sync_timedb_thread_pool(
  *,
  max_workers: int,
  thread_role: str,
  process_title: str = "sync_timedb.py",
  initializer: Callable[..., Any] | None = None,
  initargs: tuple[Any, ...] = (),
) -> SyncTimedbThreadPool:
  """
  Create a titled in-process worker pool for sync_timedb.

  Args:
    max_workers (int): Pool size.
    thread_role (str): Title role such as ingest-pool.
    process_title (str): Script name used in thread titles.
    initializer (Callable[..., Any] | None): Optional per-task setup.
    initargs (tuple[Any, ...]): Arguments for initializer.

  Returns:
    SyncTimedbThreadPool: Pool with apply_async / close / join.

  Examples:
    >>> pool = create_sync_timedb_thread_pool(
    ...   max_workers=1, thread_role="ingest-pool",
    ... )
    >>> pool.apply_async(lambda: 3).get()
    3
    >>> pool.terminate(); pool.join()
  """
  return SyncTimedbThreadPool(
      max_workers=max_workers,
      thread_role=thread_role,
      process_title=process_title,
      initializer=initializer,
      initargs=initargs,
  )


class SessionSingleFlightExecutor:
  """
  Eager max_workers=1 ThreadPoolExecutor for supervisor background roles.
  
  Attributes:
    _executor: Attribute.
    enabled: Attribute.
    process_title: Attribute.
    thread_name_prefix: Attribute.
    thread_role: Attribute.
  """

  def __init__(
    self,
    *,
    thread_name_prefix: str,
    process_title: str,
    thread_role: str,
    enabled: bool = True,
  ) -> None:
    """
    Initialize a new instance.
    
    Args:
      thread_name_prefix (str): String for thread name prefix.
      process_title (str): String for process title.
      thread_role (str): String for thread role.
      enabled (bool): Boolean flag for enabled.
    
    Returns:
      None
    
    Examples:
      >>> SessionSingleFlightExecutor("x", "x", "x", True)  # doctest: +SKIP
    """
    self.thread_name_prefix = thread_name_prefix
    self.process_title = process_title
    self.thread_role = thread_role
    self.enabled = bool(enabled)
    self._executor: Optional[ThreadPoolExecutor] = None
    if self.enabled:
      self._executor = ThreadPoolExecutor(
          max_workers=1,
          thread_name_prefix=self.thread_name_prefix,
      )

  @property
  def is_active(self) -> bool:
    """
    Return True if active.
    
    Returns:
      bool: True or False for this check.
    
    Examples:
      >>> SessionSingleFlightExecutor().is_active()  # doctest: +SKIP
    """
    return self._executor is not None

  def submit(self, fn: Callable[..., R], *args: Any, **kwargs: Any) -> Any:
    """
    Submit work to this executor.
    
    Args:
      fn (Callable[..., R]): Fn.
      *args (Any): Extra positional arguments; unused unless the callee
      documents a specific leftover protocol.
      **kwargs (Any): Extra keyword arguments forwarded to the wrapped API;
      keys and value types match that callee's signature.
    
    Returns:
      Any: Value produced by this call (type depends on inputs).
    
    Raises:
      RuntimeError: Raised when ``submit`` hits a ``RuntimeError`` failure
      path.
    
    Examples:
      >>> SessionSingleFlightExecutor().submit(None)  # doctest: +SKIP
    """
    if self._executor is None:
      raise RuntimeError(
          "SessionSingleFlightExecutor is disabled or not initialized "
          "(thread_name_prefix=%s)" % self.thread_name_prefix,
      )

    def _run() -> R:
      """
      Internal helper to run.
      
      Returns:
        R: R produced by this call.
      
      Examples:
        >>> SessionSingleFlightExecutor()._run()  # doctest: +SKIP
      """
      set_daemon_thread_title(
          "",
          script_name=self.process_title,
          role=self.thread_role,
      )
      close_old_connections()
      try:
        return fn(*args, **kwargs)
      finally:
        close_old_connections()

    return self._executor.submit(_run)

  def shutdown(self, wait: bool = True) -> None:
    """
    Shut down this object and release resources.
    
    Args:
      wait (bool): Boolean flag for wait.
    
    Returns:
      None
    
    Examples:
      >>> SessionSingleFlightExecutor().shutdown(True)  # doctest: +SKIP
    """
    if self._executor is None:
      return
    self._executor.shutdown(wait=wait)


def iter_bounded_thread_pool(
  items: Iterable[T],
  worker_fn: Callable[[T], R],
  *,
  max_workers: int,
  thread_role: Optional[str] = None,
  process_title: str = "sync_timedb.py",
) -> Iterator[Tuple[T, Optional[R], Optional[BaseException]]]:
  """
  Run ``worker_fn(item)`` with bounded parallelism.
  
  Yields ``(item, result, error)`` per completed task. ``error`` is set when the
  worker raised; ``result`` is set on success.
  
  Args:
    items (Iterable[T]): Items.
    worker_fn (Callable[[T], R]): Worker fn.
    max_workers (int): Integer value for max workers.
    thread_role (Optional[str]): Thread role, or None when absent.
    process_title (str): String for process title.
  
  Yields:
    Iterator[Tuple[T, Optional[R], Optional[BaseException]]]:
    Iterator[Tuple[T, Optional[R], Optional[BaseException]]] produced by this
    call.
  
  Examples:
    >>> iter_bounded_thread_pool(None, None, 0, None, "x")  # doctest: +SKIP
  """
  item_list = list(items)
  if not item_list:
    return
  workers = max(1, min(int(max_workers), len(item_list)))
  if workers <= 1 or len(item_list) <= 1:
    for item in item_list:
      if thread_role:
        set_daemon_thread_title(
            "",
            script_name=process_title,
            role=thread_role,
        )
      try:
        yield item, worker_fn(item), None
      except BaseException as exc:
        yield item, None, exc
    return

  def _task(item: T) -> R:
    """
    Internal helper to handle task.
    
    Args:
      item (T): Item.
    
    Returns:
      R: R produced by this call.
    
    Examples:
      >>> _task(None)  # doctest: +SKIP
    """
    if thread_role:
      set_daemon_thread_title(
          "",
          script_name=process_title,
          role=thread_role,
      )
    return worker_fn(item)

  with ThreadPoolExecutor(max_workers=workers) as executor:
    future_to_item = {
        executor.submit(_task, item): item for item in item_list
    }
    for future in as_completed(future_to_item):
      item = future_to_item[future]
      try:
        yield item, future.result(), None
      except BaseException as exc:
        yield item, None, exc


def run_bounded_thread_pool(
  items: Iterable[T],
  worker_fn: Callable[[T], R],
  *,
  max_workers: int,
  thread_role: Optional[str] = None,
  process_title: str = "sync_timedb.py",
) -> list[Tuple[T, Optional[R], Optional[BaseException]]]:
  """
  Collect ``iter_bounded_thread_pool`` results in completion order.
  
  Args:
    items (Iterable[T]): Items.
    worker_fn (Callable[[T], R]): Worker fn.
    max_workers (int): Integer value for max workers.
    thread_role (Optional[str]): Thread role, or None when absent.
    process_title (str): String for process title.
  
  Returns:
    list[Tuple[T, Optional[R], Optional[BaseException]]]: list[Tuple[T,
    Optional[R], Optional[BaseException]]] produced by this call.
  
  Examples:
    >>> run_bounded_thread_pool(None, None, 0, None, "x")  # doctest: +SKIP
  """
  return list(
      iter_bounded_thread_pool(
          items,
          worker_fn,
          max_workers=max_workers,
          thread_role=thread_role,
          process_title=process_title,
      ),
  )
