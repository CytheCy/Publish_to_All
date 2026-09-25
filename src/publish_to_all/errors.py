"""Errors suitable for displaying at the eventual CLI boundary."""


class PublishToAllError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(PublishToAllError):
    """Invalid or unreadable local configuration."""


class StoryError(PublishToAllError):
    """Missing, unreadable, or invalid story input."""


class BrowserSessionError(PublishToAllError):
    """Sanitized browser error, optionally with a local diagnostic path."""
