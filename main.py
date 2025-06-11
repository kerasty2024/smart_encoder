"""
Main entry point for the Smart Encoder application.

This script initializes the application, parses command-line arguments,
and launches the appropriate encoding pipeline based on the provided options.
It serves as the orchestrator for the entire encoding process.
"""

import os
import sys
from pathlib import Path

from loguru import logger

from src.cli import get_args
from src.config.common import LOGGER_FORMAT
from src.pipeline.video_pipeline import (
    PhoneEncodingPipeline,
    StandardVideoPipeline,
)
from src.utils.module_updater import Modules


# Configure the logger for initial setup.
# The level might be overridden later by command-line arguments.
logger.remove()
log_level = "DEBUG" if __debug__ else "INFO"
logger.add(sys.stderr, level=log_level, format=LOGGER_FORMAT)


def main():
    """
    Main function to start the encoding process.

    This function performs the following steps:
    1. Checks for and applies module updates (e.g., FFmpeg).
    2. Parses command-line arguments.
    3. Configures the global logger based on the arguments.
    4. Determines the target directory for processing.
    5. Selects and runs the appropriate encoding pipeline (Standard or Phone-specific)
       based on the parsed arguments.
    6. Logs the final completion message.
    """
    # Verify external tools and run updates if configured
    Modules.run_all()

    args = get_args()

    # Re-configure the logger based on the effective log level from arguments or debug mode.
    if __debug__ and not getattr(args, 'debug_mode', False):
        effective_log_level = "DEBUG"
    elif getattr(args, 'debug_mode', False):
        effective_log_level = "DEBUG"
    else:
        effective_log_level = args.log_level if hasattr(args, 'log_level') else "INFO"

    logger.remove()
    logger.add(sys.stderr, level=effective_log_level, format=LOGGER_FORMAT)

    logger.debug(f"Parsed arguments: {args}")

    # Determine the project path to operate on.
    project_path_str = getattr(args, 'target_dir', None)
    if project_path_str:
        project_path = Path(project_path_str).resolve()
        logger.info(f"Target directory specified: {project_path}")
    else:
        project_path = Path.cwd().resolve()
        logger.info(f"No target directory specified, using current working directory: {project_path}")

    # Select and execute the appropriate pipeline.
    if getattr(args, 'debug_iphone_mode', False):
        args.processes = 1
        args.audio_only = True
        args.move_raw_file = True
        logger.info(f"Running in iPhone debug mode with overridden args for path: {project_path}")
        phone_pipeline = PhoneEncodingPipeline(project_path, args=args)
        phone_pipeline.process_multi_file()
        phone_pipeline.post_actions()
    elif getattr(args, 'iphone_specific_task', False):
        logger.info(f"Running iPhone encoding pipeline for path: {project_path}")
        phone_pipeline = PhoneEncodingPipeline(project_path, args=args)
        phone_pipeline.process_multi_file()
        phone_pipeline.post_actions()
    else:
        logger.info(f"Running standard video encoding pipeline for path: {project_path}")
        standard_pipeline = StandardVideoPipeline(project_path, args=args)
        standard_pipeline.run()

    logger.success("Smart Encoder process finished.")


if __name__ == "__main__":
    # This environment variable can help resolve some library conflicts,
    # particularly with Intel's MKL library in environments with multiple
    # conflicting versions.
    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"
    main()