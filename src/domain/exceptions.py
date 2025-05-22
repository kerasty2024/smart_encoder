# --- Generic Base Exception ---
class SmartEncoderException(Exception):
    """Base class for all custom exceptions in the Smart Encoder application."""
    pass

# --- Preprocessing / PreEncoder Specific Exceptions ---
class PreprocessingException(SmartEncoderException):
    """Base class for exceptions raised during media file preprocessing."""
    pass

class FileAlreadyEncodedException(PreprocessingException):
    """Exception raised when a file is skipped because it is already encoded."""
    pass

class FileOversizedException(PreprocessingException):
    """Exception raised when a file is skipped due to potential oversized encoding."""
    pass

class BitRateTooLowException(PreprocessingException):
    """Exception raised when a file is skipped because its bitrate is below the threshold."""
    pass

class FormatExcludedException(PreprocessingException):
    """Exception raised when a file is skipped due to an excluded format."""
    pass

class NoStreamsFoundException(PreprocessingException):
    """Exception raised when no processable streams are found in the media file."""
    pass

class CRFSearchFailedException(PreprocessingException):
    """Exception raised when CRF search fails during preprocessing."""
    pass

class UnexpectedPreprocessingException(PreprocessingException): # Renamed from UnexpectedPreEncoderException
    """Exception raised for general or unexpected preprocessing errors."""
    pass

class NoAudioStreamException(PreprocessingException):
    """Exception raised when no suitable audio stream is found during preprocessing."""
    pass

class SkippedFileException(PreprocessingException): # Renamed from SkippedVideoFileException for generality
    """Exception raised when a file does not need to be pre-encoded or processed."""
    pass

# --- MediaFile / Probe Specific Exceptions ---
class MediaFileException(SmartEncoderException):
    """Base class for exceptions related to media file analysis (probing, duration)."""
    pass

class NoDurationFoundException(MediaFileException):
    """Exception raised when duration information cannot be obtained for a media file."""
    pass

# --- Encoding Specific Exceptions ---
class EncodingException(SmartEncoderException):
    """Base class for exceptions raised during the main encoding process."""
    pass

class MP4MKVEncodeFailException(EncodingException):
    """Exception raised when encoding to MP4 fails, and subsequently to MKV also fails (or if only MP4 was attempted)."""
    pass

# --- Original EncodeError.py content (if distinct and still needed, integrate or alias) ---
# PreVideoEncoderError was a base, now PreprocessingException can serve this.
# CRFSearchFailedError -> CRFSearchFailedException
# UnexpectedPreEncoderError -> UnexpectedPreprocessingException
# NoAudioStreamError -> NoAudioStreamException
# SkippedVideoFileError -> SkippedFileException (more general)
# NoDurationFoundError -> NoDurationFoundException (part of MediaFileException now)

# Ensure all previously defined exceptions are covered or mapped to the new hierarchy.
# For simplicity, if the old names are directly referenced often, you could alias them:
# e.g., PreVideoEncoderError = PreprocessingException
# However, it's cleaner to update call sites to use the new exception names/hierarchy.