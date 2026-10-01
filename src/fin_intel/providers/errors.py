class ProviderError(Exception):
    pass


class NotConfiguredError(ProviderError):
    """A required credential or setting is missing."""


class QuotaExceededError(ProviderError):
    """The provider's quota is used up; further calls this period will fail too."""
