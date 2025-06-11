"""
Configuration settings related to video processing.

This module defines constants for video file extensions, encoding parameters,
output directory names, error handling paths, and other video-specific settings.
"""
from pathlib import Path

from .common import BASE_ERROR_DIR, SKIPPED_DIR

# --- General Video Settings ---
DEFAULT_MAX_WORKERS = 4
VIDEO_BITRATE_LOW_THRESHOLD = 100_000
VIDEO_EXTENSIONS = (
    ".wmv", ".ts", ".mp4", ".mov", ".mpg", ".mkv", ".avi", ".iso",
    ".m2ts", ".rmvb", ".3gp", ".flv", ".vob", ".webm", ".m4v", ".asf", ".mts",
)
EXCEPT_FORMAT = {"av1"}

# --- Encoder Settings ---
ENCODERS = ["libsvtav1"]
encoders_str = "_".join(ENCODERS)
HEVC_ENCODER = "hevc_nvenc"
AV1_ENCODER = "libsvtav1"
OPUS_ENCODER = "libopus"
SKIP_VIDEO_CODEC_NAMES = ["mjpeg"]

# --- Output Directory and Tag Settings ---
VIDEO_OUT_DIR_ROOT = Path(f"{encoders_str}_encoded").resolve()
COMPLETED_RAW_DIR = Path(f"{VIDEO_OUT_DIR_ROOT}_raw").resolve()
VIDEO_COMMENT_ENCODED = "encoded_by_Kerasty"
VIDEO_OVER_SIZE_TAG_PRE_ENCODE = f"_{encoders_str}over_sized_pre_encode"
VIDEO_OVER_SIZE_TAG_ENCODED = f"_{encoders_str}over_sized_encoded"
VIDEO_ABAV1_ERROR_TAG = "_abav1_error"

# --- Error and Exclusion Directory Settings ---
VIDEO_CRF_CHECK_ERROR_DIR = BASE_ERROR_DIR / "crf_check_error"
VIDEO_NO_AUDIO_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_audio_found"
NO_DURATION_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_duration_found"

# Keywords used to exclude certain folders from being scanned for media files.
EXCEPT_FOLDERS_KEYWORDS = (
    VIDEO_OUT_DIR_ROOT.name,
    COMPLETED_RAW_DIR.name,
    SKIPPED_DIR.name,
    BASE_ERROR_DIR.name,
    "converted", "encoded", ".ab-av1-", "checked", "_raw", "TARGET_VMAF_HIGH",
)

# --- Manual and Pre-encoding Settings ---
MANUAL_ENCODE_RATE = 0.9
MANUAL_CRF = 23
MANUAL_CRF_INCREMENT_PERCENT = 15
MAX_CRF = 55
TARGET_VMAF = 95
MAX_ENCODED_PERCENT = 97
SAMPLE_EVERY = "7m"

# --- Stream Selection Settings ---
# Codecs to be re-encoded to Opus.
AUDIO_OPUS_CODECS = ("pcm", "cook", "wmav2", "wmapro", "wma", "flac")
# Subtitle codecs that are safe to copy into an MKV container.
SUBTITLE_MKV_CODECS = (
    "pgs", "ass", "ssa", "vobsub", "dvd_subtitle", "subrip", "srt",
    "hdmv_pgs_subtitle", "mov_text", "tx3g", "webvtt",
)

# --- iPhone XR Specific Profile Settings ---
MANUAL_VIDEO_BIT_RATE_IPHONE_XR = 30_000  # kbps
MANUAL_AUDIO_BIT_RATE_IPHONE_XR = 50_000  # bps
MANUAL_FPS_IPHONE_XR = 20
IPHONE_XR_OPTIONS = f" -vf scale=-1:414 -r {MANUAL_FPS_IPHONE_XR} "
VIDEO_CODEC_IPHONE_XR = "libsvtav1"
AUDIO_CODEC_IPHONE_XR = "libopus"
OUTPUT_DIR_IPHONE = (
    f"converted_{VIDEO_CODEC_IPHONE_XR}_"
    f"vbitrate_{MANUAL_VIDEO_BIT_RATE_IPHONE_XR // 1000}k_"
    f"abitrate_{MANUAL_AUDIO_BIT_RATE_IPHONE_XR // 1000}k"
)