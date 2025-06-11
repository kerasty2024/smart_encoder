"""
Common configuration settings used throughout the application.

This module contains shared constants for logging formats, directory structures,
file names, job status identifiers, and other global parameters.
It also handles loading user-specific paths from 'config.user.yaml'.
"""
from pathlib import Path
import yaml
from loguru import logger

# --- User Config Loading ---
# This block loads user-specific paths from a config file at the project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
USER_CONFIG_PATH = PROJECT_ROOT / "config.user.yaml"

# Path to the directory containing ffmpeg, ffprobe, etc. Loaded from user config.
# If None, the system's PATH will be used.
MODULE_PATH: Path | None = None

# Path to a directory containing updated versions of the tools. Loaded from user config.
# If None, the update step is skipped.
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


# --- Logging ---

# The format string for the Loguru logger, defining how log messages are displayed.
LOGGER_FORMAT = (
    "<green>{time:MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "{process} - <level>{message}</level>"
)

# --- Directories and Files ---

# The base directory where all error-related files and subdirectories are stored.
BASE_ERROR_DIR = Path("encode_error").resolve()

# A subdirectory within BASE_ERROR_DIR for files that were intentionally skipped.
SKIPPED_DIR = BASE_ERROR_DIR / Path("skipped_dir")

# A subdirectory within BASE_ERROR_DIR for media files that failed to be probed or loaded.
LOAD_FAILED_DIR = BASE_ERROR_DIR / "load_failed"

# A log file that lists all files that failed to load and their new location.
LOAD_FAILED_LOG = LOAD_FAILED_DIR / "list.txt"

# The name of the combined YAML log file that aggregates all successful encoding logs.
COMPLETED_LOG_FILE_NAME = "combined_log.yaml"

# The default name for individual success log files before they are combined.
DEFAULT_SUCCESS_LOG_YAML = "success_log.yaml"

# The name of the text file where the executed FFmpeg command is logged for debugging.
COMMAND_TEXT = "cmd.txt"

# The minimum file size in bytes. Video files smaller than this will be deleted
# as they are likely corrupted or not valid media.
MINIMUM_FILE_SIZE = 100_000

# --- Encoding and Processing ---

# The maximum number of times a failed encoding job should be retried before
# being marked as a permanent error.
MAX_ENCODE_RETRIES = 9

# The target bitrate (in bits per second) to use when converting high-resolution
# audio formats like FLAC to the Opus codec.
FLAC_TO_OPUS_BPS = 510_000

# A tuple of keywords used to identify audio or subtitle streams with a desired language.
# The codes should be in lowercase. 'und' stands for 'undetermined'.
LANGUAGE_WORDS = (
    "ja", "jp", "en", "zh", "zh-cn", "zh-tw", "chinese", "jpn",
    "eng", "zho", "chi", "und", 'japanese', 'jap',
)

# --- Job Status Constants for EncodeInfo ---
# These constants represent the various states of an encoding job,
# allowing for state management and recovery.

# Initial state of a job before any processing has begun.
JOB_STATUS_PENDING = "pending"
# Status when preprocessing (e.g., stream analysis) has started.
JOB_STATUS_PREPROCESSING_STARTED = "preprocessing_started"
# Status when the CRF (Constant Rate Factor) search has started.
JOB_STATUS_CRF_SEARCH_STARTED = "crf_search_started"
# Status when preprocessing has successfully completed.
JOB_STATUS_PREPROCESSING_DONE = "preprocessing_done"
# Status when the main FFmpeg encoding process has been invoked.
JOB_STATUS_ENCODING_FFMPEG_STARTED = "encoding_ffmpeg_started"
# Final status for a successfully completed job.
JOB_STATUS_COMPLETED = "completed"
# Status for a job that failed but can be retried.
JOB_STATUS_ERROR_RETRYABLE = "error_retryable"
# Status for a job that failed and will not be retried.
JOB_STATUS_ERROR_PERMANENT = "error_permanent"
# Status for a file that was intentionally skipped (e.g., already encoded, too small).
JOB_STATUS_SKIPPED = "skipped"