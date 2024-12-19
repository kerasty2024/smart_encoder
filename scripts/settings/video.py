from pathlib import Path

from scripts.settings.common import SKIPPED_DIR, BASE_ERROR_DIR

# General Settings
DEFAULT_MAX_WORKERS = 4  # Maximum number of workers for processing

# Video Settings
VIDEO_BITRATE_LOW_THRESHOLD = 100_000  # Low bitrate threshold for video processing
VIDEO_EXTENSIONS = (
    ".wmv",
    ".ts",
    ".mp4",
    ".mov",
    ".mpg",
    ".mkv",
    ".avi",
    ".iso",
    ".m2ts",
    ".rmvb",
    ".3gp",
    ".flv",
    ".vob",
    ".webm",
    ".m4v",
    ".asf",
    ".mts",
)  # Supported video file extensions

EXCEPT_FORMAT = {"av1"}  # Formats to exclude from processing

# Encoder Settings
ENCODERS = ["libsvtav1"]  # List of encoders to use
encoders_str = "_".join(ENCODERS)  # Concatenated string of encoders

# Output Directory Settings
VIDEO_OUT_DIR_ROOT = Path(f"{encoders_str}_encoded").resolve()
COMPLETED_RAW_DIR = Path(f"{VIDEO_OUT_DIR_ROOT}_raw").resolve()

# Tags and Comments
VIDEO_COMMENT_ENCODED = "encoded_by_Kerasty"
VIDEO_OVER_SIZE_TAG_PRE_ENCODE = f"_{encoders_str}over_sized_pre_encode"
VIDEO_OVER_SIZE_TAG_ENCODED = f"_{encoders_str}over_sized_encoded"
VIDEO_ABAV1_ERROR_TAG = "_abav1_error"

# Error Directories
VIDEO_CRF_CHECK_ERROR_DIR = BASE_ERROR_DIR / "crf_check_error"
VIDEO_NO_AUDIO_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_audio_found"
NO_DURATION_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_duration_found"

# Keywords for Exclusion
EXCEPT_FOLDERS_KEYWORDS = (
    VIDEO_OUT_DIR_ROOT.name,
    COMPLETED_RAW_DIR.name,
    SKIPPED_DIR.name,
    BASE_ERROR_DIR.name,
    "converted",
    "encoded",
    ".ab-av1-",
    "checked",
    "_raw",
    "TARGET_VMAF_HIGH",
)

# Manual Encoding Settings
MANUAL_ENCODE_RATE = 0.9  # Manual encoding rate
MANUAL_CRF = 23  # Manual CRF value
MANUAL_CRF_INCREMENT_PERCENT = 15  # Percentage increment for CRF
MAX_CRF = 55  # max limit CRF of ffmpeg command.

# Audio and Subtitle Settings
AUDIO_OPUS_CODECS = (
    "pcm",
    "cook",
    "wmav2",
    "wmapro",
    "wma",
    "flac",
)  # Codecs to convert to OPUS

SUBTITLE_MKV_CODECS = (
    "pgs",
    "ass",
    "vobsub",
    "dvd_subtitle",
    "subrip",
)  # Subtitles to output in MKV

# Encoder Types
HEVC_ENCODER = "hevc_nvenc"
AV1_ENCODER = "libsvtav1"
OPUS_ENCODER = "libopus"

# Codecs to Skip
SKIP_VIDEO_CODEC_NAMES = ["mjpeg"]  # Skip encoding for these codecs

# ab-av1 Parameters
TARGET_VMAF = 95  # Target Video Multi-Method Assessment Fusion score
MAX_ENCODED_PERCENT = 97  # Maximum encoded percentage
SAMPLE_EVERY = "7m"  # Sampling interval

# iPhone XR Settings
MANUAL_VIDEO_BIT_RATE_IPHONE_XR = 30_000  # kbps
MANUAL_AUDIO_BIT_RATE_IPHONE_XR = 50_000  # kbps
MANUAL_FPS_IPHONE_XR = 20

IPHONE_XR_OPTIONS = f" -vf scale=-1:414 -r {MANUAL_FPS_IPHONE_XR} "
VIDEO_CODEC_IPHONE_XR = "libsvtav1"
AUDIO_CODEC_IPHONE_XR = "libopus"

OUTPUT_DIR_IPHONE = (
    f"converted_{VIDEO_CODEC_IPHONE_XR}_"
    f"vbitrate_{MANUAL_VIDEO_BIT_RATE_IPHONE_XR // 1000}k_"
    f"abitrate_{MANUAL_AUDIO_BIT_RATE_IPHONE_XR // 1000}k"
)
