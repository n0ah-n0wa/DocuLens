"""DocuLens document-processing worker.

The worker is the second interface layer over ``doculens`` (SPECIFICATIONS.md §48, §72): it consumes
processing jobs from the queue port and drives the application-layer pipeline. It never serves HTTP.
"""

from importlib.metadata import version

__version__ = version("doculens-worker")
