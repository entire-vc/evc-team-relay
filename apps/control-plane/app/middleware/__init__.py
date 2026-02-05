"""Middleware package for Control Plane."""

from app.middleware.metrics import PrometheusMiddleware

__all__ = ["PrometheusMiddleware"]
