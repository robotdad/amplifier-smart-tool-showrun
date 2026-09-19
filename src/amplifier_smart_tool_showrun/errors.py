"""Only deliberate, non-sensitive errors cross the public boundary."""


class ShowrunError(Exception):
    def __init__(self, code, message, remedy="Inspect the retained take; use a new request_id after correction."):
        super().__init__(message)
        self.code, self.message, self.remedy = code, message, remedy

    def public(self):
        return {"code": self.code, "message": self.message, "remedy": self.remedy}


def require(condition, message, code="invalid_request"):
    if not condition:
        raise ShowrunError(code, message)