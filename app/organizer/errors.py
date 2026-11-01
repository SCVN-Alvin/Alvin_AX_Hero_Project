"""Application errors that map directly onto HTTP responses."""


class AppError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message, *, code=None, details=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        self.details = details or {}

    def to_dict(self):
        return {"error": {"code": self.code, "message": self.message, "details": self.details}}


class ValidationError(AppError):
    status = 400
    code = "validation_error"

    def __init__(self, message, *, field=None, details=None):
        d = dict(details or {})
        if field:
            d["field"] = field
        super().__init__(message, details=d)


class NotFound(AppError):
    status = 404
    code = "not_found"


class Conflict(AppError):
    status = 409
    code = "conflict"
