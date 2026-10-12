"""Stable error codes for module-owned translations."""


class RiderError(Exception):
    def __init__(self, code: str, status: int = 409, details=None):
        super().__init__(code)
        self.code = code
        self.status = status
        self.details = details
