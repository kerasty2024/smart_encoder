import os
import sys
from pathlib import Path

from loguru import logger

# --- Paths need to be relative to this main.py file ---
# Assuming src is a package in the same directory as main.py
from src.cli import get_args
from src.utils.module_updater import Modules
from src.pipeline.video_pipeline import (
    PhoneEncodingPipeline,
    StandardVideoPipeline,
)
from src.config.common import LOGGER_FORMAT

# Configure logger
logger.remove()
log_level = "DEBUG" if __debug__ else "INFO" # Basic check, args might override
logger.add(sys.stderr, level=log_level, format=LOGGER_FORMAT)


def main():
    """
    Main function to start the encoding process.
    Determines which pipeline to run based on arguments.
    """
    Modules.update()  # Perform module updates first
    args = get_args()

    # Override log level if debug is set via CLI or python -O
    if __debug__ and not getattr(args, 'debug_mode', False):
        effective_log_level = "DEBUG"
    elif getattr(args, 'debug_mode', False):
        effective_log_level = "DEBUG"
    else:
        effective_log_level = args.log_level if hasattr(args, 'log_level') else "INFO"

    logger.remove()
    logger.add(sys.stderr, level=effective_log_level, format=LOGGER_FORMAT)

    logger.debug(f"Parsed arguments: {args}")

    project_path_str = getattr(args, 'target_dir', None)
    if project_path_str:
        project_path = Path(project_path_str).resolve()
        logger.info(f"Target directory specified: {project_path}")
        # Potentially change CWD if the script's logic heavily relies on it,
        # though it's generally better to pass paths explicitly.
        # os.chdir(project_path) # Avoid if possible
    else:
        project_path = Path.cwd().resolve()
        logger.info(f"No target directory specified, using current working directory: {project_path}")


    if getattr(args, 'debug_iphone_mode', False):
        args.processes = 1
        # target = r"Z:\encode\iPhone\audiobook" # This was hardcoded debug path
        # project_path should now come from --target-dir if used, or CWD.
        args.audio_only = True
        args.move_raw_file = True
        args.processes = 1 # Redundant, already set
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

    logger.info("Smart Encoder process finished.")


if __name__ == "__main__":
    # Ensure the `src` package directory is in the Python path
    # if main.py is in the parent directory of `src` package.
    # This is often needed if you run `python main.py` from the project root.
    current_dir = Path(__file__).resolve().parent
    # Add the directory containing the 'src' package to sys.path
    # This assumes 'src' is a subdirectory of where main.py is.
    # If main.py is in the same dir as the 'src' folder, this is correct.
    # If you place main.py somewhere else, adjust this.
    # No, if main.py is in the root, and src is a subdir, Python should find it.
    # This might be needed if main.py was inside another subdir. Let's assume it's not needed for now
    # if it's in the project root.

    # sys.path.insert(0, str(current_dir)) # Usually not needed if structure is root/main.py and root/src/

    os.environ["KMP_DUPLICATE_LIB_OK"] = "True"
    main()