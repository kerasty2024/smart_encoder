import os
import sys
from pathlib import Path

from loguru import logger

from scripts.controllers.Appends import Modules
from scripts.controllers.functions import get_args
from scripts.controllers.start_encode_files import (
    start_encode_video_files_multi_process,
)
from scripts.settings.common import LOGGER_FORMAT

logger.remove()
log_level = "DEBUG" if __debug__ else "INFO"
logger.add(sys.stderr, level=log_level, format=LOGGER_FORMAT)


def main():
    """
    Main function to start the encoding process.
    """
    args = get_args()
    start_encode_video_files_multi_process(Path.cwd().resolve(), args)


if __name__ == "__main__":
    Modules.update()
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"
    main()
