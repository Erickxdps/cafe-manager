class AppError(Exception):
    status = 400

    def __init__(self, message, status=None):
        super().__init__(message)
        self.message = message
        if status:
            self.status = status


class ValidationError(AppError):
    status = 400


class NotFoundError(AppError):
    status = 404


class BusinessRuleError(AppError):
    """Operación válida en forma pero no permitida por las reglas (ej. sin stock)."""
    status = 409
