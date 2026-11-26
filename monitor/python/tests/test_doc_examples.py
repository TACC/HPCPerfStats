"""Run every ```python block of docs/RAW_DATA_FORMAT.md against the fixtures.

Blocks run in order in one namespace with ``path`` (archive fixture), ``shared_path``
(shared-node fixture) and ``host_dir`` (a host archive directory built from the archive
fixture). A ``print(...)   # <literal>`` line must print exactly ``<literal>``.
"""

import contextlib
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
DOC = HERE.parents[1] / "docs" / "RAW_DATA_FORMAT.md"

_BLOCK_RE = re.compile(r"```python\n(.*?)```", re.S)
_EXPECT_RE = re.compile(r"^\s*print\((.*)\)\s+#\s+(.+)$")


class DocExamplesTest(unittest.TestCase):
    def test_blocks_run_and_match_comments(self):
        blocks = _BLOCK_RE.findall(DOC.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(blocks), 8)
        checked = 0
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(HERE / "fixtures" / "archive_sample.txt", os.path.join(d, "1767225600"))
            os.link(os.path.join(d, "1767225600"), os.path.join(d, "current"))
            ns = {
                "path": str(HERE / "fixtures" / "archive_sample.txt"),
                "shared_path": str(HERE / "fixtures" / "shared_node_sample.txt"),
                "host_dir": d,
            }
            for i, block in enumerate(blocks):
                with contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(block, "RAW_DATA_FORMAT.md block %d" % i, "exec"), ns)  # noqa: S102
                for line in block.splitlines():
                    m = _EXPECT_RE.match(line)
                    if not m:
                        continue
                    comment = m.group(2).strip()
                    if comment[:1] in "[{":
                        expected = comment
                    elif comment[:1].isdigit():
                        expected = comment.split()[0]
                    else:
                        continue
                    got = str(eval(m.group(1), ns))  # noqa: S307
                    self.assertEqual(got, expected, "block %d: %s" % (i, line.strip()))
                    checked += 1
        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()
