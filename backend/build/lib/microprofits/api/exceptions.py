class CapitalAPIError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        self.status = status
        self.code = code
        super().__init__(f"[{status}] {code}: {message}")


class AuthenticationError(CapitalAPIError):
    pass


class RateLimitError(CapitalAPIError):
    pass


class OrderError(Exception):
    def __init__(self, deal_ref: str, status: str, reason: str):
        self.deal_ref = deal_ref
        self.status = status
        self.reason = reason
        super().__init__(f"Order {deal_ref} {status}: {reason}")
