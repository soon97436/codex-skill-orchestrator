class OrchestratorError(Exception):
    """Base error with a stable process exit code."""

    exit_code = 2


class ValidationError(OrchestratorError):
    exit_code = 2


class SecurityError(OrchestratorError):
    exit_code = 3


class IntegrityError(OrchestratorError):
    exit_code = 4


class OperationError(OrchestratorError):
    exit_code = 5
