"""HTTP routers.

Public application routes are versioned under ``/api/v1`` (SPECIFICATIONS.md §34). Infrastructure
probes such as ``/health`` are unversioned because they are consumed by the platform, not by users.
"""
