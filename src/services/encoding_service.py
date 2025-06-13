"""
A convenience module for importing various encoder services.

This file acts as a "barrel" or a single, clean entry point for accessing all
the primary encoder classes defined in the services package. Instead of importing
each encoder from its specific file, other parts of the application (like the
pipelines) can import them directly from this module.

Example:
    from src.services.encoding_service import VideoEncoder, AudioEncoder

This practice helps to keep the import statements tidy and decouples the consumer
of the services from the exact file structure within the package. The `__all__`
variable explicitly defines the public API of this module.
"""
from .audio_encoder import AudioEncoder
from .encoder_base import Encoder
from .phone_encoder import PhoneVideoEncoder
from .video_encoder import VideoEncoder

# For backward compatibility, so that other modules can import from this file
__all__ = ["AudioEncoder", "Encoder", "PhoneVideoEncoder", "VideoEncoder"]