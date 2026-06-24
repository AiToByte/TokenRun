"""TokenRun gateway components for resource access and privacy."""

from gateway.file_gateway import FileGateway
from gateway.mcp_client import MCPClient, MCPTool
from gateway.privacy import PrivacyRedactor
from gateway.provider import LLMProvider, LLMProviderError, LLMResponse

__all__ = [
    "FileGateway", "MCPClient", "MCPTool", "PrivacyRedactor",
    "LLMProvider", "LLMProviderError", "LLMResponse",
]

# Optional gateways (require extra dependencies)
try:
    from gateway.s3_gateway import S3Gateway  # noqa: F401
    __all__.append("S3Gateway")
except ImportError:
    pass

try:
    from gateway.sql_gateway import SQLGateway  # noqa: F401
    __all__.append("SQLGateway")
except ImportError:
    pass

try:
    from gateway.batch_provider import BatchProvider, BatchJob, BatchRequest, BatchResult  # noqa: F401
    __all__.extend(["BatchProvider", "BatchJob", "BatchRequest", "BatchResult"])
except ImportError:
    pass
