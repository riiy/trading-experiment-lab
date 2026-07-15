class TradingExperimentError(Exception):
    """Base error for Trading Experiment Lab."""


class ConfigError(TradingExperimentError):
    """Configuration validation failed."""


class PermissionDenied(TradingExperimentError):
    """Requested action is blocked by guard rails."""
