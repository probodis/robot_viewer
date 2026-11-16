"""Stable, sanitized failures at external service boundaries."""


class UpstreamUnavailable(RuntimeError):
    """A dependency cannot currently complete a request (HTTP 503)."""


class InvalidUpstreamResponse(RuntimeError):
    """A dependency returned an invalid response or rejected credentials (502)."""


class InvalidTelemetry(ValueError):
    """Stored telemetry cannot safely be used for the requested order."""


class InvalidStorageReference(ValueError):
    """An asset reference is outside the permitted machine namespace."""


class DiagnosticLogUnavailable(UpstreamUnavailable):
    """A selected local text log is unreadable or corrupt."""
