"""any-to-markdown: convert files and YouTube links into Markdown."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("any-to-markdown")
except PackageNotFoundError:  # pragma: no cover - package not installed
    __version__ = "0.0.0"

from .input_handler import MissingDependencyError, TranscriptUnavailableError
from .main import (
    ConversionResult,
    ConversionStatus,
    get_markdown,
    get_markdown_directory,
    handle_yt_local,
    handle_yt_local_async,
)

__all__ = [
    "ConversionResult",
    "ConversionStatus",
    "MissingDependencyError",
    "TranscriptUnavailableError",
    "get_markdown",
    "get_markdown_directory",
    "handle_yt_local",
    "handle_yt_local_async",
    "__version__",
]
