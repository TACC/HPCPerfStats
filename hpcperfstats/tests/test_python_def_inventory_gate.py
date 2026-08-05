"""Gate tests for scripts/python_def_inventory.py coverage contract."""

from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
INV_PATH = SCRIPTS / "python_def_inventory.py"


def _load_inventory_mod():
  """Load python_def_inventory as a module from scripts/.

  Returns:
    module: Loaded inventory module.
  """
  spec = importlib.util.spec_from_file_location(
    "python_def_inventory",
    INV_PATH,
  )
  assert spec and spec.loader
  mod = importlib.util.module_from_spec(spec)
  sys.modules["python_def_inventory"] = mod
  spec.loader.exec_module(mod)
  return mod


@pytest.fixture(scope="module")
def inv():
  """Inventory module fixture.

  Returns:
    module: ``python_def_inventory``.
  """
  return _load_inventory_mod()


def test_inventory_lists_nested_and_exclusions(inv, tmp_path: Path):
  """Nested defs are inventoried; excluded trees carry reasons."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  sample = pkg / "nested_sample.py"
  sample.write_text(
    textwrap.dedent(
      '''\
      from __future__ import annotations

      def outer(x: int) -> int:
        """Add ``x`` via a nested helper.

        Args:
          x (int): Value of ``x`` (``int``).

        Returns:
          int: Sum of ``x`` and the nested call result.

        Examples:
          >>> outer(1)
          2
        """

        def inner(y: int) -> int:
          """Add enclosing ``x`` to ``y``.

          Args:
            y (int): Value of ``y`` (``int``).

          Returns:
            int: ``x + y``.

          Examples:
            >>> inner(1)
            2
          """
          return x + y

        return inner(x)
      '''
    ),
    encoding="utf-8",
  )
  tests = tmp_path / "hpcperfstats" / "tests"
  tests.mkdir(parents=True)
  (tests / "test_foo.py").write_text("def test_x():\n  assert True\n", encoding="utf-8")
  mig = tmp_path / "hpcperfstats" / "site" / "migrations"
  mig.mkdir(parents=True)
  (mig / "0001_x.py").write_text("def forwards():\n  pass\n", encoding="utf-8")

  records = inv.build_inventory([tmp_path])
  nested = [r for r in records if r.kind == "nested" and not r.excluded]
  assert any(r.qualname.endswith("outer.inner") for r in nested)

  excluded = [r for r in records if r.excluded]
  reasons = {r.excluded_reason for r in excluded}
  assert "test_tree" in reasons or "test_module" in reasons
  assert "django_migrations" in reasons


def test_checker_fails_on_missing_args_or_hints(inv, tmp_path: Path):
  """Deliberately incomplete def fails signature and docstring checks."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "bad.py"
  bad.write_text(
    textwrap.dedent(
      """\
      def incomplete(a, b=1):
        return a + b
      """
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(
    bad,
    root=tmp_path,
    repo="HPCPerfStats",
  )
  fns = [r for r in records if r.kind != "module"]
  assert len(fns) == 1
  rec = fns[0]
  assert not rec.excluded
  assert not rec.ok
  assert any("missing_param_annotation" in i for i in rec.issues)
  assert "missing_docstring" in rec.issues or "missing_args_section" in rec.issues


def test_checker_accepts_short_dunder_template(inv, tmp_path: Path):
  """Short meaningful dunder docs are accepted with Examples."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  good = pkg / "dunder.py"
  good.write_text(
    textwrap.dedent(
      '''\
      """dunder module."""

      from __future__ import annotations

      class Box:
        """Simple labeled box container.

        Attributes:
          _label: Private label string.
        """

        def __str__(self) -> str:
          """Return the informal string form of this box.

          Returns:
            str: String representation of the box.

          Examples:
            >>> str(Box())
            'box'
          """
          return "box"

        def __init__(self) -> None:
          """Initialize the box with a default label.

          Returns:
            None

          Examples:
            >>> Box()  # doctest: +SKIP
          """
          self._label = "box"
          return None
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(good, root=tmp_path, repo="HPCPerfStats")
  in_scope = [r for r in records if not r.excluded]
  assert in_scope
  assert all(r.ok for r in in_scope), [r.issues for r in in_scope if not r.ok]


def test_inventory_script_main_check_on_fixture(inv, tmp_path: Path, capsys):
  """main(--check) returns 1 when fixtures have gaps and 0 when clean."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  (pkg / "gap.py").write_text("def g(x):\n  return x\n", encoding="utf-8")
  rc = inv.main(["--root", str(tmp_path), "--check", "--max-fail-print", "5"])
  assert rc == 1

  (pkg / "gap.py").write_text(
    textwrap.dedent(
      '''\
      """gap module."""

      from __future__ import annotations

      def g(x: int) -> int:
        """Return the input unchanged (identity).

        Args:
          x (int): Value of ``x`` (``int``).

        Returns:
          int: The input ``x`` unchanged.

        Examples:
          >>> g(1)
          1
        """
        return x
      '''
    ),
    encoding="utf-8",
  )
  rc2 = inv.main(["--root", str(tmp_path), "--check"])
  assert rc2 == 0


def test_checker_rejects_see_callers_doc_phrase(inv, tmp_path: Path):
  """Deferral phrases like 'see callers' fail the inventory gate."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "poly.py"
  bad.write_text(
    textwrap.dedent(
      '''\
      """poly module."""

      from __future__ import annotations
      from typing import Any

      def f(x: Any) -> Any:
        """Polymorphic helper.

        Args:
          x (Any): Value of ``x`` (polymorphic — see callers).

        Returns:
          Any: Result of ``f`` (polymorphic when ``Any`` — see return sites).

        Examples:
          >>> f(1)
          1
        """
        return x
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  fns = [r for r in records if r.kind == "function"]
  rec = fns[0]
  assert not rec.ok
  assert any(i.startswith("forbidden_doc_phrase:") for i in rec.issues)


def test_checker_rejects_name_echo_and_placeholder_examples(inv, tmp_path: Path):
  """Name-only summaries and ``name(...)`` Examples placeholders fail."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "echo.py"
  bad.write_text(
    textwrap.dedent(
      '''\
      """echo module."""

      from __future__ import annotations
      from typing import Any

      class Command:
        """Command."""

        def handle(self, *args: Any, **options: Any) -> None:
          """handle.

          Args:
            *args (Any): Variadic positional polymorphism: element types vary
              by call site.
            **options (Any): Keyword-arg polymorphism: arbitrary ``str`` keys
              with values whose types vary by call site.

          Returns:
            None

          Examples:
            >>> handle(...)  # doctest: +SKIP
          """
          return None
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  classes = [r for r in records if r.kind == "class"]
  methods = [r for r in records if r.kind == "method"]
  assert classes and "summary_echoes_name" in classes[0].issues
  assert methods
  assert "summary_echoes_name" in methods[0].issues
  assert "examples_placeholder_skip" in methods[0].issues
  assert any(i.startswith("forbidden_doc_phrase:") for i in methods[0].issues)


def test_checker_accepts_command_handle_docs(inv, tmp_path: Path):
  """Django-style Command.handle docs with superclass prose pass."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  good = pkg / "pg_cmd.py"
  good.write_text(
    textwrap.dedent(
      '''\
      """Management command fixture."""

      from __future__ import annotations
      from typing import Any

      class BaseCommand:
        """Framework manage.py command base (argument parse + handle)."""

      class Command(BaseCommand):
        """Print session counts for the default database.

        Subclasses Django ``BaseCommand``, which is the framework entry for
        ``manage.py`` commands. This subclass only implements ``handle``.
        """

        def handle(self, *args: Any, **options: Any) -> None:
          """Run the command body (override of ``BaseCommand.handle``).

          ``BaseCommand.handle`` is the hook Django calls after parsing options.

          Args:
            *args (Any): Unused positional leftovers from Django.
            **options (Any): Standard ``BaseCommand`` options such as
              ``verbosity`` and ``settings``. This override does not read them.

          Returns:
            None

          Examples:
            >>> # manage.py pg_connection_stats
            >>> Command().handle(verbosity=1)  # doctest: +SKIP
          """
          return None
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(good, root=tmp_path, repo="HPCPerfStats")
  in_scope = [r for r in records if not r.excluded]
  assert in_scope
  assert all(r.ok for r in in_scope), [
    (r.qualname, r.issues) for r in in_scope if not r.ok
  ]


def test_checker_rejects_ai_slop_summaries_and_args(inv, tmp_path: Path):
  """Upgrade-helper templates like Internal helper for / Value polymorphism fail."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "slop.py"
  bad.write_text(
    textwrap.dedent(
      '''\
      """slop module."""

      from __future__ import annotations
      from typing import Any, Optional
      import pandas as pd

      def _frame_usable(df: Optional[pd.DataFrame]) -> bool:
        """Internal helper for frame usable.

        Args:
          df (Optional[pd.DataFrame]): df as ``Optional[pd.DataFrame]``.

        Returns:
          bool: Result of ``_frame_usable`` as ``bool``.

        Examples:
          >>> _frame_usable(None)  # doctest: +SKIP
        """
        return False

      def combine(value: Any) -> Any:
        """Compute or apply combine.

        Args:
          value (Any): Value polymorphism: mapping, sequence, model instance,
            or scalar accepted by branch logic.

        Returns:
          Any: Open polymorphism for ``value``.

        Examples:
          >>> combine(1)  # doctest: +SKIP
        """
        return value
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  fns = {r.name: r for r in records if r.kind == "function"}
  assert "summary_ai_slop" in fns["_frame_usable"].issues or any(
    i.startswith("forbidden_doc_phrase:internal helper for")
    for i in fns["_frame_usable"].issues
  )
  assert "args_as_ann_slop" in fns["_frame_usable"].issues or any(
    "result of ``" in i for i in fns["_frame_usable"].issues
  )
  assert any(
    i.startswith("forbidden_doc_phrase:") for i in fns["combine"].issues
  )


def test_checker_rejects_overlong_signature_line(inv, tmp_path: Path):
  """Single-line def headers longer than Ruff line-length fail the gate."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "long_sig.py"
  bad.write_text(
    '"""long_sig module."""\n\n'
    "from __future__ import annotations\n\n"
    "def very_long_name_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa("
    "x: int) -> int:\n"
    '  """Return the input unchanged.\n\n'
    "  Args:\n"
    "    x (int): Value of ``x``.\n\n"
    "  Returns:\n"
    "    int: The input ``x``.\n\n"
    "  Examples:\n"
    "    >>> very_long_name_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa(1)\n"
    '  """\n'
    "  return x\n",
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  fns = [r for r in records if r.kind == "function"]
  rec = fns[0]
  assert not rec.ok
  assert any(i.startswith("signature_line_too_long:") for i in rec.issues)


def test_checker_requires_raises_and_examples(inv, tmp_path: Path):
  """Raises entries and Examples >>> prompts are hard-gated."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "raises_ex.py"
  bad.write_text(
    textwrap.dedent(
      '''\
      """raises_ex module."""

      from __future__ import annotations

      def boom(x: int) -> int:
        """Boom helper.

        Args:
          x (int): Value.

        Returns:
          int: Value.
        """
        if x < 0:
          raise ValueError("neg")
        return x
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  fns = [r for r in records if r.kind == "function"]
  assert any("missing_raises_section" in i for i in fns[0].issues)
  assert any("missing_examples_section" in i for i in fns[0].issues)


def test_checker_requires_class_private_attributes(inv, tmp_path: Path):
  """Class Attributes must list private attrs assigned in __init__."""
  pkg = tmp_path / "hpcperfstats" / "lib"
  pkg.mkdir(parents=True)
  bad = pkg / "attrs.py"
  bad.write_text(
    textwrap.dedent(
      '''\
      """attrs module."""

      from __future__ import annotations

      class Thing:
        """Demo type missing Attributes entries.

        Attributes:
          public: Public mirror of ``n``.
        """

        def __init__(self, n: int) -> None:
          """Store ``n`` on the instance.

          Args:
            n (int): Size.

          Returns:
            None

          Examples:
            >>> Thing(1)  # doctest: +SKIP
          """
          self._n = n
          self.public = n
      '''
    ),
    encoding="utf-8",
  )
  records = inv.inventory_file(bad, root=tmp_path, repo="HPCPerfStats")
  classes = [r for r in records if r.kind == "class"]
  assert classes
  assert not classes[0].ok
  assert any("missing_attributes" in i for i in classes[0].issues)


def test_raised_exception_names_and_init_attrs(inv):
  """AST helpers collect named raises and private init attrs."""
  import ast

  tree = ast.parse(
    textwrap.dedent(
      """\
      class C:
        def __init__(self):
          self._a = 1
          self.b = 2
          x = 3
        def f(self):
          raise ValueError("x")
          raise
      """
    )
  )
  cls = tree.body[0]
  attrs = inv.collect_class_instance_attrs(cls)
  assert attrs == ["_a", "b"]
  fn = cls.body[1]
  raised = inv.collect_raised_exception_names(fn)
  assert "ValueError" in raised
  assert "Exception" in raised


def test_inventory_check_green(inv):
  """Full-tree --check must pass for every in-scope surface in both repos."""
  workspace = REPO_ROOT.parent
  roots = inv.default_roots(workspace)
  records = inv.build_inventory(roots)
  failed = inv.failing_records(records)
  assert not failed, (
    f"{len(failed)} in-scope docstring/hint failures; first="
    f"{failed[0].path}:{failed[0].lineno} {failed[0].qualname} {failed[0].issues}"
  )
