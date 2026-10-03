class ProviderError(Exception):
    pass


class NotConfiguredError(ProviderError):
    """A required credential or setting is missing."""


class QuotaExceededError(ProviderError):
    """The provider's quota is used up; further calls this period will fail too."""


class NotFoundError(ProviderError):
    """The resource doesn't exist (HTTP 404), e.g. a daily index for a market holiday."""
