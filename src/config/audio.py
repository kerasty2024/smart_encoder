"""
Configuration settings related to video processing.

This module defines constants for video file extensions, encoding parameters,
output directory names, error handling paths, and other video-specific settings.
"""
from pathlib import Path

from .common import BASE_ERROR_DIR, SKIPPED_DIR

# --- General Video Settings ---

# The default maximum number of parallel processes to use for encoding.
DEFAULT_MAX_WORKERS = 4

# The bitrate threshold (in bits per second) below which video files are skipped.
# This helps to avoid re-encoding already highly compressed files.
VIDEO_BITRATE_LOW_THRESHOLD = 100_000

# A base tuple of file extensions that are recognized as video files.
_BASE_VIDEO_EXTENSIONS = (
    ".wmv", ".ts", ".mp4", ".mov", ".mpg", ".mkv", ".avi", ".iso",
    ".m2ts", ".rmvb", ".3gp", ".flv", ".vob", ".webm", ".m4v", ".asf", ".mts",
)

# Automatically generate a list of extensions with the '.!qb' suffix
# to include files that are currently being downloaded (e.g., by qBittorrent).
_QB_VIDEO_EXTENSIONS = tuple(f"{ext}.!qb" for ext in _BASE_VIDEO_EXTENSIONS)

# The final, combined tuple of all supported video extensions.
VIDEO_EXTENSIONS = _BASE_VIDEO_EXTENSIONS + _QB_VIDEO_EXTENSIONS


# A set of video codec names (in lowercase) to exclude from processing.
# For example, 'av1' files will be skipped if they are already in that format.
EXCEPT_FORMAT = {"av1"}

# --- Encoder Settings ---

# A list of video encoders to be considered during the pre-encoding (CRF search) phase.
# The first encoder in this list is used as a fallback.
ENCODERS = ["libsvtav1"]
encoders_str = "_".join(ENCODERS) # A string representation for use in directory names.

# Specific encoder names used in the application logic.
HEVC_ENCODER = "hevc_nvenc"
AV1_ENCODER = "libsvtav1"
OPUS_ENCODER = "libopus"

# A list of video codec names to skip encoding for. For example, 'mjpeg' (often
# found in camera footage) might not be suitable for this encoding workflow.
SKIP_VIDEO_CODEC_NAMES = ["mjpeg"]

# --- Output Directory and Metadata Settings ---

# The root directory name for storing encoded video files.
VIDEO_OUT_DIR_ROOT = Path(f"{encoders_str}_encoded").resolve()

# The root directory for storing the original (raw) files after they have been
# successfully encoded and the --move-raw-file flag is used.
COMPLETED_RAW_DIR = Path(f"{VIDEO_OUT_DIR_ROOT}_raw").resolve()

# A standard comment to embed in the metadata of encoded video files.
VIDEO_COMMENT_ENCODED = "encoded_by_Kerasty"

# --- Error and Exclusion Directory Settings ---

# A subdirectory within BASE_ERROR_DIR for videos that had no suitable audio stream found.
VIDEO_NO_AUDIO_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_audio_found"

# A subdirectory within BASE_ERROR_DIR for videos where a duration could not be determined.
NO_DURATION_FOUND_ERROR_DIR = BASE_ERROR_DIR / "no_duration_found"

# A tuple of keywords. If any of these keywords appear in a directory's path,
# that directory (and its subdirectories) will be excluded from the file scan.
# This is used to prevent the encoder from processing its own output folders.
EXCEPT_FOLDERS_KEYWORDS = (
    VIDEO_OUT_DIR_ROOT.name,
    COMPLETED_RAW_DIR.name,
    SKIPPED_DIR.name,
    BASE_ERROR_DIR.name,
    "converted", "encoded", ".ab-av1-", "checked", "_raw", "TARGET_VMAF_HIGH",
)

# --- Manual and Pre-encoding Settings ---

# The default CRF (Constant Rate Factor) to use when --manual-mode is enabled.
# Lower values mean higher quality and larger file size.
MANUAL_CRF = 23

# The maximum CRF value to use in FFmpeg commands. This acts as a safeguard
# to prevent extremely low quality outputs.
MAX_CRF = 55

# The target VMAF (Video Multi-Method Assessment Fusion) score for the `ab-av1`
# pre-encoding CRF search. VMAF is a perceptual quality metric.
TARGET_VMAF = 95

# The maximum allowed encoded file size as a percentage of the original file size
# during the `ab-av1` CRF search. This prevents selecting a CRF that bloats the file.
MAX_ENCODED_PERCENT = 97

# The interval at which `ab-av1` should take samples from the video for its analysis.
# Format is a time string like '10s', '5m', etc.
SAMPLE_EVERY = "7m"

# --- Stream Selection Settings ---

# A tuple of audio codec names that should be re-encoded to the Opus codec
# for better compression and compatibility.
AUDIO_OPUS_CODECS = ("pcm", "cook", "wmav2", "wmapro", "wma", "flac")

# A tuple of subtitle codec names that are considered safe to copy directly
# into an MKV container without re-encoding.
SUBTITLE_MKV_CODECS = (
    "pgs", "ass", "ssa", "vobsub", "dvd_subtitle", "subrip", "srt",
    "hdmv_pgs_subtitle", "mov_text", "tx3g", "webvtt",
)

# --- iPhone XR Specific Profile Settings ---

# The manual video bitrate (in kilobits per second) for the iPhone XR profile.
MANUAL_VIDEO_BIT_RATE_IPHONE_XR = 30_000

# The manual audio bitrate (in bits per second) for the iPhone XR profile.
MANUAL_AUDIO_BIT_RATE_IPHONE_XR = 50_000

# The target frames per second (FPS) for the iPhone XR profile.
MANUAL_FPS_IPHONE_XR = 20

# A string of FFmpeg video filter options for the iPhone XR profile,
# including scaling (`-vf`) and frame rate (`-r`).
IPHONE_XR_OPTIONS = f" -vf scale=-1:414 -r {MANUAL_FPS_IPHONE_XR} "

# The video codec to use for the iPhone XR profile.
VIDEO_CODEC_IPHONE_XR = "libsvtav1"

# The audio codec to use for the iPhone XR profile.
AUDIO_CODEC_IPHONE_XR = "libopus"

# The name of the output directory for files encoded with the iPhone XR profile.
# The name is generated from the profile's settings for easy identification.
OUTPUT_DIR_IPHONE = (
    f"converted_{VIDEO_CODEC_IPHONE_XR}_"
    f"vbitrate_{MANUAL_VIDEO_BIT_RATE_IPHONE_XR // 1000}k_"
    f"abitrate_{MANUAL_AUDIO_BIT_RATE_IPHONE_XR // 1000}k"
)