"""
Common configuration settings used throughout the application.

This module contains globally shared configuration settings and constants that are
used across the entire Smart Encoder application. It centralizes parameters for
logging, file and directory management, encoding behavior, and job status tracking.
It also handles the loading of user-specific configurations from an external
YAML file, allowing for easy customization without modifying the source code.
"""
from pathlib import Path
import yaml
from loguru import logger

# --- User-Defined Path Configuration ---
# This block loads user-specific paths from a 'config.user.yaml' file located
# at the project root. This allows users to specify the locations of external
# tools like FFmpeg without hardcoding paths.

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
USER_CONFIG_PATH = PROJECT_ROOT / "config.user.yaml"

# The directory containing the FFmpeg and ffprobe executables. This is loaded from
# 'config.user.yaml'. If not provided or None, the application assumes the
# executables are available in the system's PATH.
MODULE_PATH: Path | None = None

# The directory where updated versions of external tools (like FFmpeg) are placed.
# The application will copy files from this directory to `MODULE_PATH` on startup.
# If not provided or None, the module update step is skipped.
MODULE_UPDATE_PATH: Path | None = None

if USER_CONFIG_PATH.is_file():
    try:
        with USER_CONFIG_PATH.open("r", encoding="utf-8") as f:
            user_config = yaml.safe_load(f)
        if user_config and "paths" in user_config:
            paths_config = user_config.get("paths") or {}
            ffmpeg_dir_str = paths_config.get("ffmpeg_dir")
            update_dir_str = paths_config.get("module_update_dir")

            if ffmpeg_dir_str:
                MODULE_PATH = Path(ffmpeg_dir_str)
            if update_dir_str:
                MODULE_UPDATE_PATH = Path(update_dir_str)
    except Exception as e:
        logger.warning(f"Could not load or parse '{USER_CONFIG_PATH}': {e}")
else:
    logger.debug(f"User config '{USER_CONFIG_PATH}' not found. Relying on system PATH for executables.")


# --- Logging Configuration ---
# Settings related to application-wide logging.

# The format string for the Loguru logger. It defines the structure and appearance
# of log messages, including timestamp, level, module name, and the message itself.
LOGGER_FORMAT = (
    "<green>{time:MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "{process} - <level>{message}</level>"
)

# The length of the random string appended to temporary, dated success log files.
# This prevents filename collisions when multiple encoder processes run concurrently
# and write logs at the same time.
SUCCESS_LOG_RANDOM_LENGTH = 10


# --- Directory and File Management ---
# Constants defining the structure for output, error, and temporary files.

# The root directory for storing all files related to encoding errors.
# This helps to centralize and organize problematic files for later inspection.
BASE_ERROR_DIR = Path("encode_error").resolve()

# A subdirectory within `BASE_ERROR_DIR` for files that were intentionally
# skipped (e.g., already encoded, too small) but not due to a processing error.
SKIPPED_DIR = BASE_ERROR_DIR / Path("skipped_dir")

# A subdirectory within `BASE_ERROR_DIR` for media files that could not be read
# or probed by ffprobe, often indicating file corruption or an unsupported format.
LOAD_FAILED_DIR = BASE_ERROR_DIR / "load_failed"

# A log file within `LOAD_FAILED_DIR` that records the original and new paths of
# files that failed to load, along with the reason for the failure.
LOAD_FAILED_LOG = LOAD_FAILED_DIR / "list.txt"

# The filename for the final, consolidated YAML log file that aggregates all
# individual success logs into one comprehensive report.
COMPLETED_LOG_FILE_NAME = "combined_log.yaml"

# The default filename for individual success log files before they are combined.
# This is used when a dated, randomized filename is not generated.
DEFAULT_SUCCESS_LOG_YAML = "success_log.yaml"

# The filename for the text file that logs the exact FFmpeg command executed
# for a particular encoding job. This is extremely useful for debugging.
COMMAND_TEXT = "cmd.txt"

# The minimum file size in bytes for a media file to be considered for processing.
# Files smaller than this (100 KB) are often assumed to be invalid, empty, or
# corrupted, and are therefore skipped or deleted.
MINIMUM_FILE_SIZE = 100_000


# --- Encoding and Processing Rules ---
# General parameters that control the encoding and file processing logic.

# The maximum number of times the application will retry a failed encoding job
# before marking it as a permanent, non-retryable error.
MAX_ENCODE_RETRIES = 9

# The target bitrate (in bits per second) used when converting high-resolution
# lossless audio (like FLAC) to the lossy Opus codec. This is a general setting,
# distinct from specific profiles like the iPhone one.
FLAC_TO_OPUS_BPS = 510_000

# A tuple of lowercase keywords and language codes (ISO 639-1, 639-2) used to
# identify desired audio or subtitle streams. 'und' for 'undetermined' is included
# to allow for language detection on streams without explicit language tags.
LANGUAGE_WORDS = (
    "ja", "jp", "en", "zh", "zh-cn", "zh-tw", "chinese", "jpn",
    "eng", "zho", "chi", "und", 'japanese', 'jap',
)


# --- Job Status Constants ---
# These constants represent the various states of an encoding job, which allows
# for robust state management, recovery, and restartability of the pipeline.
# They are used by the `EncodeInfo` model to track progress.

JOB_STATUS_PENDING = "pending"  # Initial state before any processing begins.
JOB_STATUS_PREPROCESSING_STARTED = "preprocessing_started"  # Media analysis has started.
JOB_STATUS_CRF_SEARCH_STARTED = "crf_search_started"  # The CRF search to find optimal quality has started.
JOB_STATUS_PREPROCESSING_DONE = "preprocessing_done"  # Preprocessing is complete, ready for FFmpeg.
JOB_STATUS_ENCODING_FFMPEG_STARTED = "encoding_ffmpeg_started"  # The main FFmpeg encoding process is running.
JOB_STATUS_COMPLETED = "completed"  # The job finished successfully.
JOB_STATUS_ERROR_RETRYABLE = "error_retryable"  # An error occurred, but the job can be retried.
JOB_STATUS_ERROR_PERMANENT = "error_permanent"  # A non-recoverable error occurred; will not be retried.
JOB_STATUS_SKIPPED = "skipped"  # The file was intentionally skipped (e.g., already encoded).