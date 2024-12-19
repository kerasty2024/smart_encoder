class VideoEncoderException(Exception):
    """Base class for all exceptions raised by PreVideoEncoder."""

    pass


class MP4MKVEncodeFailException(VideoEncoderException):
    """Exception raised when original file suffix is mp4."""
    pass
