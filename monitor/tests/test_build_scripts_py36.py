#!/usr/bin/env python3
"""Build-time Python scripts must run on Python 3.6 (EL8 system python3).

The build-time set is derived, not hand-listed: every `python3 ... scripts/<x>.py`
invocation in the Automake files, the RPM spec and the shell scripts that prepare
and run the static/RPM build, plus the local modules those scripts import.
`make check` helpers are not build-time (rpmbuild has no %check).
"""

import ast
import os
import re
import subprocess
import tempfile
import tokenize
import unittest
from pathlib import Path

MONITOR = Path(__file__).resolve().parents[1]
SCRIPTS = MONITOR / "scripts"

BUILD_DRIVER_GLOBS = (
    "Makefile.am",
    "src/Makefile.am",
    "hpcperfstats.spec",
    "scripts/build_static_bundle.sh",
    "scripts/prepare_rpmbuild*.sh",
    "scripts/ensure_dotbuild_prefix_static.sh",
    "scripts/lib/*.sh",
)

_RE_PY_INVOCATION = re.compile(r"\bpython3?\b[^\n]*?\b(scripts/[\w./-]+\.py)\b")
_PEP585_BUILTINS = {"dict", "list", "tuple", "set", "frozenset", "type"}
_PY37_KWARGS = {"capture_output", "text"}
_PY37_MODULES = {"dataclasses", "contextvars", "importlib.resources", "graphlib", "zoneinfo"}
_PY37_ATTRS = {"fromisoformat", "removeprefix", "removesuffix", "nullcontext", "isascii"}


def build_driver_files():
    files = []
    for pattern in BUILD_DRIVER_GLOBS:
        files.extend(sorted(MONITOR.glob(pattern)))
    return files


def build_time_scripts():
    """Scripts invoked by build drivers, plus their local imports (transitively)."""
    found = set()
    for path in build_driver_files():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            for rel in _RE_PY_INVOCATION.findall(line):
                found.add(MONITOR / rel)
    pending = list(found)
    while pending:
        tree = ast.parse(pending.pop().read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            for name in names:
                candidate = SCRIPTS / (name.replace(".", "/") + ".py")
                if candidate.is_file() and candidate not in found:
                    found.add(candidate)
                    pending.append(candidate)
    return sorted(found)


def _annotation_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = node.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None and arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign):
            yield node.annotation


def _fstring_debug_specifiers(src):
    """Line numbers of f'{x=}' (3.8+); needs the 3.12+ f-string tokenizer."""
    if not hasattr(tokenize, "FSTRING_START"):
        return []
    toks = list(tokenize.generate_tokens(iter(src.splitlines(True)).__next__))
    hits = []
    depth = 0
    for i, tok in enumerate(toks):
        if tok.type == tokenize.FSTRING_START:
            depth += 1
        elif tok.type == tokenize.FSTRING_END:
            depth -= 1
        elif depth and tok.type == tokenize.OP and tok.string == "=":
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and nxt.string in ("}", "!", ":"):
                hits.append(tok.start[0])
    return hits


def py36_violations(path):
    """Constructs in `path` that Python 3.6 cannot compile or run."""
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path), feature_version=(3, 6))
    except SyntaxError as exc:
        return ["{}:{}: {}".format(path.name, exc.lineno, exc.msg)]
    out = []
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        # feature_version rejects := only from Python 3.10 (EL9 python3 is 3.9).
        if isinstance(node, ast.NamedExpr):
            out.append("{}:{}: assignment expression := (3.8+)".format(path.name, line))
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            if any(a.name == "annotations" for a in node.names):
                out.append("{}:{}: from __future__ import annotations".format(path.name, line))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for mod in mods:
                if mod in _PY37_MODULES:
                    out.append("{}:{}: import {} (3.7+)".format(path.name, line, mod))
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in _PY37_KWARGS:
                    out.append("{}:{}: keyword {}= (3.7+)".format(path.name, line, kw.arg))
        if isinstance(node, ast.Attribute) and node.attr in _PY37_ATTRS:
            out.append("{}:{}: .{} (3.7+)".format(path.name, line, node.attr))
    for ann in _annotation_nodes(tree):
        for sub in ast.walk(ann):
            if isinstance(sub, ast.BinOp) and isinstance(sub.op, ast.BitOr):
                out.append("{}:{}: PEP 604 X | Y annotation".format(path.name, sub.lineno))
            if (
                isinstance(sub, ast.Subscript)
                and isinstance(sub.value, ast.Name)
                and sub.value.id in _PEP585_BUILTINS
            ):
                out.append("{}:{}: PEP 585 {}[...] annotation".format(path.name, sub.lineno, sub.value.id))
    for ln in _fstring_debug_specifiers(src):
        out.append("{}:{}: f-string '=' specifier (3.8+)".format(path.name, ln))
    return out


def _py36_interpreter():
    for cand in (os.environ.get("PY36"), "python3.6", "/usr/libexec/platform-python", "python3"):
        if not cand:
            continue
        try:
            ver = subprocess.run(
                [cand, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                universal_newlines=True,
            ).stdout.strip()
        except OSError:
            continue
        if ver == "3.6":
            return cand
    return None


class BuildScriptsPy36Test(unittest.TestCase):
    def test_build_time_script_list_is_derived(self):
        names = [p.name for p in build_time_scripts()]
        self.assertIn("emit_build_capabilities.py", names)

    def test_build_time_scripts_parse_as_python36(self):
        problems = []
        for path in build_time_scripts():
            problems.extend(py36_violations(path))
        self.assertEqual(problems, [], "\n".join(problems))

    def test_checker_rejects_py37_constructs(self):
        bad = (
            "from __future__ import annotations\n",
            "def f(x: dict[str, int]) -> None: pass\n",
            "def f(x: 'int') -> str | None: pass\n",
            "y = (z := 1)\n",
            "import subprocess\nsubprocess.run(['x'], capture_output=True)\n",
            "from dataclasses import dataclass\n",
        )
        if hasattr(tokenize, "FSTRING_START"):
            bad += ("x = 1\ny = f'{x=}'\n",)
        with tempfile.TemporaryDirectory() as tmp:
            for i, src in enumerate(bad):
                path = Path(tmp) / "bad{}.py".format(i)
                path.write_text(src, encoding="utf-8")
                self.assertNotEqual(py36_violations(path), [], src)
            ok = Path(tmp) / "ok.py"
            ok.write_text(
                "from typing import Optional\ndef f(x: Optional[int]) -> int:\n"
                "    return f'{x}' and 1\n",
                encoding="utf-8",
            )
            self.assertEqual(py36_violations(ok), [])

    def test_emit_build_capabilities_runs_under_python36(self):
        py36 = _py36_interpreter()
        if py36 is None:
            self.skipTest("no Python 3.6 interpreter (set PY36=/path/to/python3.6)")
        with tempfile.TemporaryDirectory() as tmp:
            build = Path(tmp)
            (build / "src").mkdir()
            (build / "src" / "Makefile").write_text(
                "AM_CPPFLAGS = -DMONITOR_WITH_HARDWARE -DMONITOR_CPU_BACKEND_X86\n",
                encoding="utf-8",
            )
            header = build / "src" / "monitor_capability_slug.h"
            proc = subprocess.run(
                [py36, str(SCRIPTS / "emit_build_capabilities.py"), "--build-dir", str(build),
                 "--header", str(header)],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout)
            self.assertIn("MONITOR_CAPABILITY_SLUG", header.read_text(encoding="utf-8"))
            self.assertTrue((build / "monitor-build-capabilities.json").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
