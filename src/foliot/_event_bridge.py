"""One internal exception shared across the optional Event boundary."""

__all__ = ["EventConfigurationError"]


class EventConfigurationError(RuntimeError):
    """Event API was used without a matching Event-enabled configuration."""
