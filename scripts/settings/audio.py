# Supported audio file extensions
from pathlib import Path

AUDIO_EXTENSIONS = (".flac", ".wav", ".mp3", ".opus", ".m4a", ".m4b")

# Audio encoding settings
DEFAULT_AUDIO_ENCODER = "libopus"
TARGET_BIT_RATE_IPHONE_XR = 50_000  # bits per second

# Directory paths for encoded audio files
AUDIO_ENCODED_ROOT_DIR = Path(
    f"Encoded_{DEFAULT_AUDIO_ENCODER}_{TARGET_BIT_RATE_IPHONE_XR // 1000}kbps"
)
AUDIO_ENCODED_RAW_DIR = Path(f"{AUDIO_ENCODED_ROOT_DIR}_raw")

# Metadata comment for encoded audio
AUDIO_COMMENT_ENCODED = "encoded_by_Kerasty"
