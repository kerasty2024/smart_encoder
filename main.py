import argparse
import os
import shutil
import sys
from pathlib import Path

from loguru import logger

from scripts.controllers.Appends import Modules
from scripts.controllers.start_encode_files import (
    start_encode_video_files_multi_process,
)
from scripts.settings.common import LOGGER_FORMAT

logger.remove()
log_level = "DEBUG" if __debug__ else "INFO"
logger.add(sys.stderr, level=log_level, format=LOGGER_FORMAT)


def get_args():
    """
    Parses command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Smart Encoder for video files.")
    parser.add_argument(
        "--processes", type=int, default=4, help="Number of processes to use."
    )
    parser.add_argument(
        "--random", action="store_true", help="encode files in random order."
    )
    parser.add_argument(
        "--not-rename", action="store_true", help="Do not rename files after encoding."
    )
    parser.add_argument(
        "--audio-only", action="store_true", help="Process only audio files."
    )
    parser.add_argument(
        "--move-raw-file", action="store_true", help="Move raw files after processing."
    )
    parser.add_argument(
        "--manual-mode",
        action="store_true",
        help="Run in manual mode with fixed paths.",
    )
    parser.add_argument(
        "--av1-only", action="store_true", help="Encode using AV1 codec only."
    )
    parser.add_argument(
        "--keep-mtime", action="store_true", help="Encode using AV1 codec only."
    )
    return parser.parse_args()


def main():
    """
    Main function to start the encoding process.
    """
    args = get_args()
    # Debug mode setup
    if __debug__:
        target = Path(r"Z:\encode\Reduce size\Test\target").resolve()
        raw = Path(r"Z:\encode\Reduce size\Test\Test_raw").resolve()

        if target.exists():
            shutil.rmtree(target)

        shutil.copytree(raw, target)

        os.chdir(target)
        args.manual_mode = False
        args.move_raw_file = True

    start_encode_video_files_multi_process(Path.cwd().resolve(), args)


if __name__ == "__main__":
    Modules.update()
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"
    main()
