"""
Configuration settings related to audio processing.

This module defines constants for audio file extensions, default encoding
parameters, and directory naming conventions for audio output. These settings
are primarily used in the audio-only and iPhone-specific encoding pipelines.
"""
from pathlib import Path

# ======================================================================================
# Audio File Identification
# ======================================================================================

# A tuple of common audio file extensions that the application will recognize and process.
# These are standard formats that are likely to be found as source files.
_BASE_AUDIO_EXTENSIONS = (".flac", ".wav", ".mp3", ".opus", ".m4a", ".m4b")

# A tuple of extensions for partially downloaded files, specifically from the qBittorrent client.
# Appending `.!qb` to the base extensions allows the application to identify and potentially
# ignore or wait on audio files that are still in the process of being downloaded.
_QB_AUDIO_EXTENSIONS = tuple(f"{ext}.!qb" for ext in _BASE_AUDIO_EXTENSIONS)

# The final, combined tuple of all supported audio extensions. This is the constant
# that should be used throughout the application to check if a file is a supported audio file.
AUDIO_EXTENSIONS = _BASE_AUDIO_EXTENSIONS + _QB_AUDIO_EXTENSIONS


# ======================================================================================
# Audio Encoding Parameters
# ======================================================================================

# The default audio codec to use for encoding. `libopus` is a highly efficient,
# open-source, and versatile audio codec, making it an excellent choice for
# balancing quality and file size, especially for mobile profiles.
DEFAULT_AUDIO_ENCODER = "libopus"

# The target audio bitrate in bits per second (bps) for the iPhone XR profile.
# 50,000 bps (50 kbps) is chosen as a good balance between perceptible audio quality
# and minimizing file size, which is important for mobile devices with limited storage.
TARGET_BIT_RATE_IPHONE_XR = 50_000


# ======================================================================================
# Directory and Metadata Settings
# ======================================================================================

# The root directory name for storing encoded audio files from the iPhone pipeline.
# The name is generated dynamically based on the encoder and bitrate for clarity and
# organization, e.g., "Encoded_libopus_50kbps".
AUDIO_ENCODED_ROOT_DIR = Path(
    f"Encoded_{DEFAULT_AUDIO_ENCODER}_{TARGET_BIT_RATE_IPHONE_XR // 1000}kbps"
)

# The directory where original (raw) audio files are moved after a successful
# encoding. This is only used when the `--move-raw-file` command-line argument
# is active. It helps in keeping the source directory clean and archives the originals.
AUDIO_ENCODED_RAW_DIR = Path(f"{AUDIO_ENCODED_ROOT_DIR}_raw")

# A standard comment string to embed in the metadata of the encoded audio files.
# This serves as a "watermark" to easily identify files that have been processed
# by this application. This information can be used to prevent re-encoding files
# in the future.
AUDIO_COMMENT_ENCODED = "encoded_by_Kerasty"