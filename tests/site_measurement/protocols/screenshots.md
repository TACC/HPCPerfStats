# Full-page Job List and Job Detail screenshots (AUTHOR_NOTES item 6)

Reviewer 3 asked for one or two figures of the **entire** Job List / Job Detail UI.
The `.tex` currently includes plot crops, not a full portal shell.

## Filenames

Save into `tests/site_measurement/results/screenshots/`:

| File | Content |
|---|---|
| `job_list_full.png` | Job List (Extended Search results) at desktop width, full page |
| `job_detail_full.png` | Job Detail for a representative job, full page including tabs |

Optional print-quality PDF of the same two views.

Then copy the PNGs next to `HUST26-062824.tex` if Overleaf should compile them, and add `\includegraphics` in a revision — that step is a paper edit, not this kit.

## How to capture

1. Sign in as staff on the production (or staging) portal.
2. Open Job List with a typical 7-day filter so the table is populated.
3. Browser full-page screenshot:
   - Firefox: `Cmd+Shift+S` then “Save full page”, or Developer Tools → screenshot.
   - Chrome: DevTools Command Menu → “Capture full size screenshot”.
4. Open a Job Detail URL whose Performance Data column is ready (prewarmed plots).
5. Repeat full-page capture. Do not crop to a single Bokeh figure.

Do **not** invent UI mockups. If you cannot capture production, leave the files missing; `emit_paper_facts.py` will keep those slots `NOT_MEASURED`.
