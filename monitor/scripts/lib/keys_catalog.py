"""Static catalog of every emitted (type, key) pair, read from the monitor C sources.

Each collector declares ``struct stats_type { .st_name = ..., .st_schema_def = JOIN(<MACRO>) }``
and ``<MACRO>`` expands to ``X(key, "options", "description")`` entries, possibly through other
macros and ``#if`` branches. This module resolves those macros without a compiler:

- a type's ``KEYS``-like macro is looked up in its own ``.c`` file first, then in the local
  headers that file includes (recursively), then in every ``src`` file;
- every ``#if`` branch of a macro contributes its entries, tagged with the preprocessor
  condition in force (so arch- or build-specific keys are listed with their condition);
- a key is slow tier (``R=S``) when its options say so, when ``collect_tier.c`` lists it, or
  when its name ends in ``_error`` / ``_errors`` (the same rules as ``collect_tier.c``).

Used by ``scripts/gen_metrics_reference.py``; standard library only, Python 3.6+.
"""

import re
from collections import OrderedDict

_DEFINE_RE = re.compile(r"^\s*#\s*define\s+(\w+)(\([^)]*\))?\s*(.*)$")
_IF_RE = re.compile(r"^\s*#\s*(if|ifdef|ifndef|elif|else|endif)\b\s*(.*)$")
_INCLUDE_RE = re.compile(r'^\s*#\s*include\s+"([^"]+)"')
_GUARD_RE = re.compile(r"^!defined\(_*[A-Z0-9_]+_H_*\)$")
_TYPE_BLOCK_RE = re.compile(r"struct\s+stats_type\s+(\w+)\s*=\s*\{(.*?)\n\s*\};", re.S)
_ST_NAME_RE = re.compile(r"\.st_name\s*=\s*([^,]+),")
_SCHEMA_RE = re.compile(r"\.st_schema_def\s*=\s*JOIN\((\w+)\)")
_TOKEN_RE = re.compile(
    r'\bX\(\s*(?P<xkey>\w+)\s*,\s*"(?P<xopts>[^"]*)"\s*,\s*(?P<xdesc>(?:"(?:[^"\\]|\\.)*"\s*)+)'
    r"|\b(?P<ident>[A-Z_][A-Z0-9_]+)\b(?:\s*\((?P<args>[^()]*)\))?"
)
_SLOW_ROW_RE = re.compile(r'\{\s*"(\w+)"\s*,\s*"(\w+)"\s*\}')
_STR_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


def _strip_comments(text):
    text = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


def _logical_lines(text):
    """Join backslash-continued lines."""
    buf = []
    for line in text.splitlines():
        if line.endswith("\\"):
            buf.append(line[:-1])
            continue
        buf.append(line)
        yield " ".join(buf)
        buf = []
    if buf:
        yield " ".join(buf)


def _scan_defines(text):
    """Return {name: [(params, body, condition)]} for every #define in ``text``.

    ``params`` is None for an object-like macro, else the parameter names."""
    out = OrderedDict()
    stack = []
    for line in _logical_lines(text):
        m = _IF_RE.match(line)
        if m:
            kind, arg = m.group(1), m.group(2).strip()
            if kind == "ifdef":
                stack.append("defined(%s)" % arg)
            elif kind == "ifndef":
                stack.append("!defined(%s)" % arg)
            elif kind == "if":
                stack.append(arg)
            elif kind == "elif" and stack:
                stack[-1] = "!(%s) && (%s)" % (stack[-1], arg)
            elif kind == "else" and stack:
                stack[-1] = "!(%s)" % stack[-1]
            elif kind == "endif" and stack:
                stack.pop()
            continue
        m = _DEFINE_RE.match(line)
        if m:
            cond = " && ".join(c for c in stack if not _GUARD_RE.match(c))
            params = None
            if m.group(2) is not None:
                params = [a.strip() for a in m.group(2)[1:-1].split(",") if a.strip()]
            out.setdefault(m.group(1), []).append((params, m.group(3), cond))
    return out


class _Source(object):
    def __init__(self, src_dir):
        paths = sorted(list(src_dir.glob("*.c")) + list(src_dir.glob("*.h")))
        self.text = {
            p.name: _strip_comments(p.read_text(encoding="utf-8", errors="replace")) for p in paths
        }
        self.defines = {n: _scan_defines(t) for n, t in self.text.items()}
        self.includes = {
            n: [m.group(1) for m in map(_INCLUDE_RE.match, t.splitlines()) if m]
            for n, t in self.text.items()
        }

    def _search_order(self, filename):
        seen, order, todo = set(), [], [filename]
        while todo:
            name = todo.pop(0)
            if name in seen or name not in self.defines:
                continue
            seen.add(name)
            order.append(name)
            todo.extend(self.includes.get(name, []))
        order.extend(n for n in sorted(self.defines) if n not in seen)
        return order

    def lookup(self, filename, name):
        """Definitions of ``name`` visible from ``filename`` (first file that defines it)."""
        for f in self._search_order(filename):
            if name in self.defines[f]:
                return self.defines[f][name]
        return []


def _substitute(body, params, args):
    """Expand one function-like macro body: ``##`` pasting, ``#p`` stringizing, plain ``p``."""
    for p, a in zip(params, args):
        body = re.sub(r"\s*##\s*%s\b" % p, a, body)
        body = re.sub(r"\b%s\s*##\s*" % p, a, body)
        body = re.sub(r"(?<![#\w])#\s*%s\b" % p, '"%s"' % a, body)
        body = re.sub(r"\b%s\b" % p, a, body)
    return body


def _expand(source, filename, body, cond, depth, out):
    """Append (key, options, description, condition) for every X(...) reachable from body,
    in emit order (macros are expanded where they appear)."""
    if depth > 12:
        raise ValueError("%s: KEYS macro nesting too deep" % filename)
    for m in _TOKEN_RE.finditer(body):
        if m.group("xkey"):
            desc = "".join(_STR_RE.findall(m.group("xdesc")))
            out.append((m.group("xkey"), m.group("xopts"), desc, cond))
            continue
        ident, call_args = m.group("ident"), m.group("args")
        for params, sub_body, sub_cond in source.lookup(filename, ident):
            if params is not None:
                if call_args is None:
                    continue
                sub_body = _substitute(
                    sub_body, params, [a.strip() for a in call_args.split(",")]
                )
            joined = " && ".join(c for c in (cond, sub_cond) if c)
            _expand(source, filename, sub_body, joined, depth + 1, out)


def _resolve_st_name(source, filename, expr):
    expr = expr.strip()
    m = _STR_RE.fullmatch(expr)
    if m:
        return m.group(1)
    for _params, body, _cond in source.lookup(filename, expr):
        m = _STR_RE.search(body)
        if m:
            return m.group(1)
    raise ValueError("%s: cannot resolve st_name %s" % (filename, expr))


def is_auto_slow(key):
    """``collect_tier.c`` auto-rule: keys ending in ``_error`` / ``_errors`` are slow."""
    return key.endswith("_error") or key.endswith("_errors")


def load_catalog(src_dir):
    """Return an OrderedDict ``type -> {"sources": [...], "keys": OrderedDict}`` sorted by type.

    Each key maps to ``{"options", "description", "conditions", "slow"}``: ``options`` is the
    KEYS option string (``E,W=48,U=B``), ``description`` the KEYS description (often empty),
    ``conditions`` the sorted preprocessor conditions it is declared under (empty list when it
    is declared unconditionally), ``slow`` the two-tier slow flag.
    """
    source = _Source(src_dir)
    slow_table = set(_SLOW_ROW_RE.findall(source.text["collect_tier.c"]))
    types = OrderedDict()
    for name in sorted(source.text):
        if not name.endswith(".c"):
            continue
        for block in _TYPE_BLOCK_RE.finditer(source.text[name]):
            nm, sm = _ST_NAME_RE.search(block.group(2)), _SCHEMA_RE.search(block.group(2))
            if nm is None or sm is None:
                continue
            st_name = _resolve_st_name(source, name, nm.group(1))
            entries = []
            for _params, macro_body, cond in source.lookup(name, sm.group(1)):
                _expand(source, name, macro_body, cond, 0, entries)
            if not entries:
                raise ValueError("%s: no X(...) keys found for %s" % (name, st_name))
            t = types.setdefault(st_name, {"sources": [], "keys": OrderedDict()})
            if name not in t["sources"]:
                t["sources"].append(name)
            for key, opts, desc, cond in entries:
                k = t["keys"].setdefault(
                    key, {"options": opts, "description": desc, "conditions": set()}
                )
                k["conditions"].add(cond)
                if desc and not k["description"]:
                    k["description"] = desc
                opt_list = [o.strip().upper() for o in opts.split(",")]
                k["slow"] = "R=S" in opt_list or (st_name, key) in slow_table or is_auto_slow(key)
    for t in types.values():
        for k in t["keys"].values():
            k["conditions"] = [] if "" in k["conditions"] else sorted(k["conditions"])
    return OrderedDict(sorted(types.items()))
