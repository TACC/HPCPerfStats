# Vista LSPCI profile dumps

Canonical queue-class `lspci` captures for Vista platforms.
Vendored for future Vista shm validate (`--system vista` / `tacc_system_profiles.py`).
Stampede3 one-binary validate remains the active operator path.

| File | Shape (from capture)                              |
| ---- | ------------------------------------------------- |
| `gg` | Grace-class bridges; ConnectX-7; no GH200 3D line |
| `gh` | Grace-class + PEX890xx; ConnectX-7; GH200 3D      |

This directory (in git) is the authoritative path.
Do not depend on workspace-root `Vista-LSPCI profiles/`.

To refresh a profile, run `lspci` on a representative Vista node of each class and replace the matching basename.
Keep basenames `gg` / `gh` stable.
