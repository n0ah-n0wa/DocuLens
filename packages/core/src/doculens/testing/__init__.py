"""Test support shared by every package's test suite (factories for valid domain entities).

Nothing here is imported by production code paths; it lives in the package so that the API and
worker test suites can reuse it without test directories becoming importable packages.
"""
