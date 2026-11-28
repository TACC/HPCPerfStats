# hpcperfstats-tools

Command-line tools and a Python client that query and feed an [HPCPerfStats](https://github.com/TACC/hpcperfstats) deployment through its REST API.
This page is for users who run the tools on a login node and for developers who change the client.

The package lives in the HPCPerfStats checkout at `hpcperfstats-tools/`; develop and install it from there.
The standalone `TACC/hpcperfstats-tools` repository is an archive only.

## Install

Requires Python 3.14+.
From the checkout root:

```bash
python3 -m pip install ./hpcperfstats-tools
```

For development, `python3 -m pip install -e ./hpcperfstats-tools` installs it editable.

## Configure

The tools read the API base URL from the INI file named by `HPCPERFSTATS_TOOLS_INI`; `hpcperfstats-tools.ini.example` is the template:

```ini
[API]
base_url = http://localhost:8000/api/
```

For a deployed portal, use its URL, such as `https://stats.cluster.domain.edu/api/`.

Both tools authenticate with an API key from the web UI (`/api-key/`), sent in the `X-API-Key` header.
A run that passes `--api-key` caches the key in `~/.hpcperfstats-api` for that base URL; later runs use the cached key.

## Run hpcperfstats-jobstats

`hpcperfstats-jobstats` prints a job-efficiency summary for one Slurm job (CPU and GPU utilization, memory) from the same data as the web UI:

```bash
hpcperfstats-jobstats <JOB_ID>
hpcperfstats-jobstats --api-url <API_URL> --api-key <API_KEY> \
  <JOB_ID>
```

`<JOB_ID>`: the Slurm job ID; `<API_URL>`: the API base URL; `<API_KEY>`: your API key.

| Option       | Description                                  |
| ------------ | -------------------------------------------- |
| `jid`        | Job ID (required)                            |
| `--api-url`  | API base URL (default from the INI file)     |
| `--api-key`  | API key (optional; otherwise the cached key) |
| `--insecure` | Disable TLS certificate verification         |

## Run hpcperfstats-sacct-gen

`hpcperfstats-sacct-gen` runs Slurm `sacct` for a date range and POSTs each day to the API ingest endpoint, or with `-f DIR` writes `DIR/YYYY-MM-DD.txt` files instead.
Usage, the staff key it needs and the HTTP 409 guard are in [Step 11: Load accounting data](../docs/tutorials/install-hpcperfstats.md#step-11-load-accounting-data).

## Use the Python client

```python
from hpcperfstats_tools import ApiClient, get_job_full_dataframe

client = ApiClient("https://stats.cluster.edu/api/", api_key="<API_KEY>")
home = client.get_json("home/")
df = get_job_full_dataframe(
  jid="12345",
  api_url="https://stats.cluster.edu/api/",
  api_key="<API_KEY>",
)
```

`ApiClient` handles auth headers, JSON calls and redirect-safe text POSTs.
`get_job_full_dataframe()` builds a pandas DataFrame from a job's metadata and type-detail sample series.

## REST path contract

`ApiClient` takes paths relative to `base_url`.
The route list is `urlpatterns` in `hpcperfstats/site/lib/machine/api_urls.py`; a change that adds or renames a machine API route updates this client and its tests in the same commit.

## License

LGPL-2.1, the same as HPCPerfStats; see [LICENSE](LICENSE).
