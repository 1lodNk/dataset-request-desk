"""Domain errors. Services raise these; one handler in main.py turns them into JSON responses
with a stable machine-readable `code` next to the human `detail`."""


class DomainError(Exception):
    status_code = 400
    code = "error"

    def __init__(self, message: str, *, code: str | None = None, **extra):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.extra = extra


class Unauthorized(DomainError):
    status_code = 401
    code = "unauthorized"


class Forbidden(DomainError):
    status_code = 403
    code = "forbidden"


class NotFound(DomainError):
    status_code = 404
    code = "not_found"


class Conflict(DomainError):
    status_code = 409
    code = "conflict"


class InvalidTransition(Conflict):
    code = "invalid_transition"


class Invalid(DomainError):
    status_code = 422
    code = "invalid"


class PayloadTooLarge(DomainError):
    status_code = 413
    code = "payload_too_large"
