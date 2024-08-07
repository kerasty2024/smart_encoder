class PreVideoEncoderError(Exception):
    """Base class for all exceptions raised by PreVideoEncoder."""

    pass


class CRFSearchFailedError(PreVideoEncoderError):
    """Exception raised when CRF search fails."""

    pass


class UnexpectedPreEncoderError(PreVideoEncoderError):
    """Exception raised for general encoder-related errors."""

    pass


class NoAudioStreamError(PreVideoEncoderError):
    """Exception raised when no suitable audio stream is found."""

    pass


class SkippedVideoFileError(PreVideoEncoderError):
    """Exception raised when no video file need to be pre-encoded."""

    pass
