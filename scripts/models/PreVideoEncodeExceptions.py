class PreVideoEncoderException(Exception):
    """Base class for all exceptions raised by PreVideoEncoder."""

    pass


class FileAlreadyEncodedException(Exception):
    """Exception raised when a file is skipped because it is already encoded."""
    pass


class FileOversizedException(Exception):
    """Exception raised when a file is skipped due to potential oversized encoding."""
    pass


class BitRateTooLowException(Exception):
    """Exception raised when a file is skipped because its bitrate is below the threshold."""
    pass


class FormatExcludedException(Exception):
    """Exception raised when a file is skipped due to an excluded format."""
    pass


class NoStreamsFoundException(Exception):
    """Exception raised when no streams are found in the media file."""
    pass


class CRFSearchFailedException(PreVideoEncoderException):
    """Exception raised when CRF search fails."""

    pass


class UnexpectedPreEncoderException(PreVideoEncoderException):
    """Exception raised for general encoder-related errors."""

    pass


class NoAudioStreamException(PreVideoEncoderException):
    """Exception raised when no suitable audio stream is found."""

    pass


class SkippedVideoFileException(PreVideoEncoderException):
    """Exception raised when no video file need to be pre-encoded."""

    pass


class NoDurationFoundException(PreVideoEncoderException):
    """Exception raised when no duration info got."""

    pass
