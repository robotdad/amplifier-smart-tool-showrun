"""Only deliberate, non-sensitive errors cross the public boundary."""


class ShowrunError(Exception):
    def __init__(self, code, message, remedy="Inspect the retained take; use a new request_id after correction.",
                 *, diagnostics=None):
        super().__init__(message)
        self.code, self.message, self.remedy = code, message, remedy
        # Only library-constructed, non-sensitive fields belong here, never raw exceptions.
        self.diagnostics = diagnostics

    def public(self):
        result = {"code": self.code, "message": self.message, "remedy": self.remedy}
        if self.diagnostics is not None:
            result["diagnostics"] = self.diagnostics
        return result


def require(condition, message, code="invalid_request"):
    if not condition:
        raise ShowrunError(code, message)