class ApplicationError(Exception):
    """Expected use-case failure safe to translate at an adapter boundary."""


class NotFoundError(ApplicationError):
    pass


class ConflictError(ApplicationError):
    pass


class AuthorizationError(ApplicationError):
    pass
