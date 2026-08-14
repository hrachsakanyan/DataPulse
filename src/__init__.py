"""DataPulse — an API-to-insights pipeline.

collect (:mod:`~src.fetch`) -> clean (:mod:`~src.clean`) ->
analyse (:mod:`~src.analyze`) -> visualise (:mod:`~src.visualize`) ->
export (:mod:`~src.report`)
"""

__version__ = "1.0.0"

__all__ = ["config", "fetch", "clean", "analyze", "visualize", "report", "theme"]
