"""
Defines custom exception types for the Smart Encoder application.

These exceptions allow for more specific error handling and clearer expression
of what went wrong during the encoding pipeline, from preprocessing and
media file analysis to the final encoding step.
"""

class SmartEncoderException(Exception):
    """Base class for all custom exceptions in the Smart Encoder application."""
    pass

# --- Preprocessing / PreEncoder Specific Exceptions ---
class PreprocessingException(SmartEncoderException):
    """Base class for exceptions raised during media file preprocessing."""
    pass

class FileAlreadyEncodedException(PreprocessingException):
    """Raised when a file is skipped because it appears to be already encoded."""
    pass

class FileOversizedException(PreprocessingException):
    """Raised when a file is skipped due to its potential for an oversized output."""
    pass

class BitRateTooLowException(PreprocessingException):
    """Raised when a file is skipped because its bitrate is below the configured threshold."""
    pass

class FormatExcludedException(PreprocessingException):
    """Raised when a file is skipped because its format is in the exclusion list."""
    pass

class NoStreamsFoundException(PreprocessingException):
    """Raised when no processable streams (e.g., video) are found in the media file."""
    pass

class CRFSearchFailedException(PreprocessingException):
    """Raised when the CRF (Constant Rate Factor) search fails during preprocessing."""
    pass

class UnexpectedPreprocessingException(PreprocessingException):
    """Raised for general or unexpected errors during the preprocessing stage."""
    pass

class NoAudioStreamException(PreprocessingException):
    """Raised when no suitable audio stream is found during preprocessing."""
    pass

class SkippedFileException(PreprocessingException):
    """Raised when a file is intentionally skipped and does not need to be processed further."""
    pass

# --- MediaFile / Probe Specific Exceptions ---
class MediaFileException(SmartEncoderException):
    """Base class for exceptions related to media file analysis (e.g., probing)."""
    pass

class NoDurationFoundException(MediaFileException):
    """Raised when duration information cannot be obtained for a media file."""
    pass

# --- Encoding Specific Exceptions ---
class EncodingException(SmartEncoderException):
    """Base class for exceptions raised during the main encoding process."""
    pass

class MP4MKVEncodeFailException(EncodingException):
    """Raised when encoding to MP4 fails, and a subsequent attempt to encode to MKV also fails."""
    pass