"""
csvql/exceptions.py — All custom exceptions
"""


class CsvqlError(Exception):
    """Base exception for all csvql errors."""


class CsvqlSyntaxError(CsvqlError):
    """Raised when the SQL string cannot be parsed."""


class CsvqlFileError(CsvqlError):
    """Raised when a CSV file cannot be found or read."""


class CsvqlColumnError(CsvqlError):
    """Raised when a referenced column does not exist."""


class CsvqlTypeError(CsvqlError):
    """Raised when a type conversion fails at runtime."""