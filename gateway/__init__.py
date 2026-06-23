"""TokenRun gateway components for resource access and privacy."""

from gateway.file_gateway import FileGateway
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMProvider, LLMProviderError, LLMResponse

__all__ = [
    "FileGateway", "PrivacyRedactor",
    "LLMProvider", "LLMProviderError", "LLMResponse",
]

# Optional gateways (require extra dependencies)
try:
    from gateway.s3_gateway import S3Gateway
    __all__.append("S3Gateway")
except ImportError:
    pass

try:
    from gateway.sql_gateway import SQLGateway
    __all__.append("SQLGateway")
except ImportError:
    pass

try:
    from gateway.batch_provider import BatchProvider, BatchJob, BatchRequest, BatchResult
    __all__.extend(["BatchProvider", "BatchJob", "BatchRequest", "BatchResult"])
except ImportError:
    pass
