"""
Defines custom exception types for the Smart Encoder application.

These exceptions allow for more specific and expressive error handling throughout
the encoding pipeline. Instead of catching a generic `Exception`, the application
can catch specific exceptions like `NoAudioStreamException` or `BitRateTooLowException`
and react accordingly. This makes the code cleaner, more robust, and easier to debug.

All custom exceptions inherit from the base `SmartEncoderException`.
"""


class SmartEncoderException(Exception):
    """Base class for all custom exceptions in the Smart Encoder application."""

    pass


# --- Preprocessing / PreEncoder Specific Exceptions ---
class PreprocessingException(SmartEncoderException):
    """Base class for exceptions raised during the media file preprocessing stage."""

    pass


class FileAlreadyEncodedException(PreprocessingException):
    """
    Raised when a file is skipped because it appears to be already encoded.

    This helps prevent re-encoding files that have already been processed,
    saving significant time and computational resources. The check is typically
    based on metadata comments embedded in the file.
    """

    pass


class FileOversizedException(PreprocessingException):
    """
    Raised when a file is skipped due to its potential for an oversized output.

    This exception is part of a strategy to avoid creating encoded files that
    are larger than the original, which can sometimes happen with very high
    quality or inefficiently compressed sources.
    """

    pass


class BitRateTooLowException(PreprocessingException):
    """
    Raised when a file's bitrate is below a configured threshold.

    This is a quality control measure to avoid processing files that are already
    highly compressed. Re-encoding such files often leads to a further
    degradation in quality with little to no benefit in file size.
    """

    pass


class FormatExcludedException(PreprocessingException):
    """
    Raised when a file is skipped because its format is in the exclusion list.

    For example, if the target encoding format is AV1, any source files that
    are already in AV1 format will be skipped, raising this exception.
    """

    pass


class NoStreamsFoundException(PreprocessingException):
    """
    Raised when no processable streams (e.g., video streams) are found.

    This can happen with corrupted files or files that do not contain any
    media data that the application is configured to handle.
    """

    pass


class CRFSearchFailedException(PreprocessingException):
    """
    Raised when the CRF (Constant Rate Factor) search fails.

    The CRF search is a preliminary step to find the optimal quality/size
    balance. If this automated step fails for any reason (e.g., external
    tool error), this exception is raised to signal a problem in the
    preprocessing phase.
    """

    pass


class UnexpectedPreprocessingException(PreprocessingException):
    """
    Raised for general or unexpected errors during the preprocessing stage.

    This serves as a catch-all for any issue during preprocessing that is not
    covered by a more specific exception type.
    """

    pass


class NoAudioStreamException(PreprocessingException):
    """
    Raised when no suitable audio stream is found during preprocessing.

    "Suitability" can be determined by language, codec, or other criteria.
    This exception allows the application to either halt processing or proceed
    with a video-only encode if configured to do so.
    """

    pass


class SkippedFileException(PreprocessingException):
    """
    Raised when a file is intentionally skipped and requires no further processing.

    This is not strictly an error but a control flow mechanism. It signals
    to the pipeline that the file has been handled (e.g., moved to a 'skipped'
    folder) and that processing for this file should cease gracefully.
    """

    pass


# --- MediaFile / Probe Specific Exceptions ---
class MediaFileException(SmartEncoderException):
    """
    Base class for exceptions related to media file analysis (e.g., probing with ffprobe).
    """

    pass


class NoDurationFoundException(MediaFileException):
    """
    Raised when duration information cannot be obtained for a media file.

    Duration is a critical piece of metadata required for almost all encoding
    operations. A file without a determinable duration is considered invalid
    and cannot be processed.
    """

    pass


# --- Encoding Specific Exceptions ---
class EncodingException(SmartEncoderException):
    """Base class for exceptions raised during the main FFmpeg encoding process."""

    pass


class MP4MKVEncodeFailException(EncodingException):
    """
    Raised when encoding to MP4 fails, and a subsequent attempt to encode to MKV also fails.

    This signals a persistent failure in the core encoding step, even after
    attempting a fallback to a more flexible container format (MKV).
    """

    pass