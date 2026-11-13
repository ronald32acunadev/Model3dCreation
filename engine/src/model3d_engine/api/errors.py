"""Errors the API answers with an ErrorBody."""


class ApiError(Exception):
    """An error answered with an ErrorBody and an HTTP status."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
