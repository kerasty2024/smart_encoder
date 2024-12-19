from pathlib import Path

# Logging configuration
LOGGER_FORMAT = (
    "<green>{time:MM-DD HH:mm:ss}</green> | "
    "<level>{level: <8}</level> | "
    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
    "{process} - <level>{message}</level>"
)

# Directory and file paths
BASE_ERROR_DIR = Path("encode_error").resolve()  # Base error directory

# Specific error directories and logs
SKIPPED_DIR = BASE_ERROR_DIR / Path("skipped_dir")  # Directory for skipped files
LOAD_FAILED_DIR = BASE_ERROR_DIR / "load_failed"  # Directory for load failures
LOAD_FAILED_LOG = LOAD_FAILED_DIR / "list.txt"  # Log file for load failures
ERROR_LOG_FILE = BASE_ERROR_DIR / "error.yaml"  # Log file for errors

# Log files
COMPLETED_LOG_FILE_NAME = "combined_log.yaml"  # Log file name for completed processes
DEFAULT_SUCCESS_LOG_YAML = "success_log.yaml"  # Default success log file
COMPLETED_FOLDERS_LOG = "completed_folders.txt"  # Log file for completed folders
COMMAND_TEXT = "cmd.txt"  # Command text file

# Configuration values
SUCCESS_LOG_RANDOM_LENGTH = (
    10  # Random length to mitigate logging issues in multi-process environments
)

# Language codes, must be in lower cases
LANGUAGE_WORDS = (
    "ja",
    "jp",
    "en",
    "zh",
    "zh-cn",
    "zh-tw",
    "chinese",
    "jpn",
    "eng",
    "zho",
    "chi",
    "und",
    'japanese',
    'jap',
)

# Bitrate settings
FLAC_TO_OPUS_BPS = 510_000  # Bits per second for FLAC to OPUS conversion

# Module paths
MODULE_PATH = Path(r"C:\Tools\bin")  # Path for the tools module
MODULE_UPDATE_PATH = Path(r"C:\Tools\updater")  # Path for the updater module

# files
MINIMUM_FILE_SIZE = 100_000  # video files lower than this size will be deleted. unit: B
