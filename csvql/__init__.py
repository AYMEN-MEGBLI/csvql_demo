"""
csvql — Blazing-fast SQL queries on CSV files.
Powered by a C core with Python ThreadPoolExecutor for parallelism.
"""

from .api.query import query, QueryBuilder
from .api.result import CsvqlResult
from .exceptions import (
    CsvqlError,
    CsvqlSyntaxError,
    CsvqlFileError,
    CsvqlColumnError,
)

__version__ = "0.1.0"
__author__  = "csvql contributors"
__all__     = [
    "query",
    "QueryBuilder",
    "CsvqlResult",
    "CsvqlError",
    "CsvqlSyntaxError",
    "CsvqlFileError",
    "CsvqlColumnError",
]