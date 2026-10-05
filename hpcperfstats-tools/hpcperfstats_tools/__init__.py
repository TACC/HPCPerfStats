"""
Utility tools for interacting with an HPCPerfStats deployment.
"""

from __future__ import annotations

from .api_client import ApiClient, ApiResult
from .job_dataframe import get_job_full_dataframe

__all__ = ["ApiClient", "ApiResult", "__version__", "get_job_full_dataframe"]

__version__ = "0.2.0"
