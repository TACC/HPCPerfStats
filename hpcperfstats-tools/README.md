# hpcperfstats-tools

Command-line tools for querying and feeding an [HPCPerfStats](https://github.com/TACC/hpcperfstats) deployment. These tools run independently of the main Django portal and talk to it via the REST API.

## Installation

```bash
pip install .
# or from PyPI when published:
# pip install hpcperfstats-tools
```

Requires Python 3.10+.

## Configuration

Tools read the API base URL from a small INI config file.

**Config file:** Set the `HPCPERFSTATS_TOOLS_INI` environment variable to the path of your INI file (e.g. the path to `hpcperfstats-tools.ini.example` or a copy of it).

```ini
[API]
# Base URL for the HPCPerfStats REST API
base_url = http://localhost:8000/api/
```

For a deployed portal, use the real base URL, e.g. `https://stats.cluster.domain.edu/api/`.

## REST paths (contract with the server)

`ApiClient` accepts relative paths under the configured `base_url`. When the Django app adds or renames machine API routes, update this client (and its tests) in the same change as the server. **Canonical route list:** `hpcperfstats/site/machine/api_urls.py` in the main HPCPerfStats repository (`urlpatterns`).

## API key

Both tools authenticate with an API key. Keys are created in the HPCPerfStats web UI (e.g. `/api-key/` or `/login_prompt` after signing in).

- **First use:** pass the key with `--api-key`. It will be stored in `~/.hpcperfstats-api` for that API base URL.
- **Later runs:** the cached key is used automatically, or you can pass `--api-key` again.
- Tools send API keys via request headers (`X-API-Key`), not query-string parameters.

## Tools

### hpcperfstats-jobstats

Prints a job-efficiency style summary for a single Slurm job (CPU/GPU utilization, memory, etc.) using the same data as the HPCPerfStats web UI.

```bash
# Summary for job 12345 (uses config base_url and cached API key)
hpcperfstats-jobstats 12345

# Override API URL and pass key explicitly
hpcperfstats-jobstats --api-url https://stats.cluster.edu/api/ --api-key YOUR_KEY 12345

# Skip TLS verification (e.g. dev with self-signed cert)
hpcperfstats-jobstats --insecure 12345
```

**Options:**

| Option       | Description |
|-------------|-------------|
| `jid`       | Job ID (required). |
| `--api-url` | Base URL for the API (default from config). |
| `--api-key` | API key (optional; otherwise uses cache). |
| `--insecure` | Disable TLS certificate verification. |

### hpcperfstats-sacct-gen

Runs `sacct` for a date range in one of two **mutually exclusive** modes:

- **API mode** (default): POSTs the pipe-delimited output to the HPCPerfStats ingest endpoint. The API stores the data using the same logic as the portal’s sync_acct and also writes `{acct_path}/YYYY-MM-DD.txt` for scheduled `sync_acct.py` reingest. The API returns HTTP 409 when a POST would replace an on-disk file with fewer lines (staff API key required).
- **File mode** (`-f DIR`): writes the same pipe-delimited body to `DIR/YYYY-MM-DD.txt` locally (same naming as the ingest API). `DIR` must already exist. No API URL or key required. Do not combine with `--api-key`.

```bash
# Ingest today only (default API mode)
hpcperfstats-sacct-gen

# Ingest a specific date range (end date exclusive)
hpcperfstats-sacct-gen 2024-01-01 2024-01-08

# Pass API key (and cache it for next time)
hpcperfstats-sacct-gen --api-key YOUR_KEY 2024-01-01 2024-01-02

# Write daily .txt files instead of POSTing (DIR must exist)
hpcperfstats-sacct-gen -f /path/to/accounting 2024-01-01 2024-01-08
```

**Options:**

| Option       | Description |
|-------------|-------------|
| `start_date` | Start date (YYYY-MM-DD or parseable). Default: today. |
| `end_date`   | End date, exclusive. Default: start + 1 day. |
| `-f DIR`     | Write `DIR/YYYY-MM-DD.txt` files instead of API ingest. Mutually exclusive with `--api-key`. |
| `--api-key`  | API key (optional in API mode; otherwise uses cache). Mutually exclusive with `-f`. |

**Requirements:**

- Slurm `sacct` on the machine where the command runs.
- **API mode:** `[API] base_url` in your tools INI (see Configuration); staff-capable API key for the ingest endpoint.
- **File mode:** `-f DIR` must be an existing directory.

## Python library usage

`hpcperfstats-tools` also exposes reusable client helpers:

```python
from hpcperfstats_tools import ApiClient, get_job_full_dataframe

client = ApiClient("https://stats.cluster.edu/api/", api_key="YOUR_KEY")
home = client.get_json("home/")

df = get_job_full_dataframe(
    jid="12345",
    api_url="https://stats.cluster.edu/api/",
    api_key="YOUR_KEY",
)
print(df.head())
```

- `ApiClient` centralizes auth headers, JSON calls, and redirect-safe text POST.
- `get_job_full_dataframe()` builds a pandas DataFrame for a job by fetching
  job metadata and type-detail sample series over the REST API.

## License

GNU Lesser General Public License v2.1 (LGPL-2.1), same as the main HPCPerfStats package. See [LICENSE](LICENSE).
