"""Explicitly public errors crossing the desktop API boundary."""


class PublicError(RuntimeError):
    """An actionable message deliberately written without source credentials."""


def public_message(error):
    return str(error) if isinstance(error, PublicError) else type(error).__name__
