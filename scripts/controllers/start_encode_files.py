import argparse
import concurrent.futures
import random
import shutil
import traceback
from pathlib import Path

from loguru import logger

from scripts.models.Encoder import VideoEncoder
from scripts.models.Log import SuccessLog
from scripts.models.MediaFile import MediaFile
from scripts.models.PreVideoEncodeExceptions import NoDurationFoundException
from scripts.models.ProcessFiles import ProcessVideoFiles
from scripts.settings.video import VIDEO_OUT_DIR_ROOT, NO_DURATION_FOUND_ERROR_DIR


def start_encode_video_files_multi_process(path: Path, args: argparse.Namespace = None):
    """
    Encodes multiple video files concurrently using a process pool.

    :param path: Directory path containing video files to be processed.
    :param args: Command-line arguments containing processing configurations.
    """
    logger.debug(f"Starting video encoding in path: {path}")
    process_files = ProcessVideoFiles(path, args)
    pre_and_post_actions(process_files, path)

    if not process_files.source_dir:
        logger.info("No source directory found, exiting process.")
        return

    logger.info(f"Remaining files to process: {len(process_files.files)}")

    try:
        with concurrent.futures.ProcessPoolExecutor(
                max_workers=args.processes
        ) as executor:
            files_to_process = process_files.files
            if args.random:
                files_to_process = random.sample(
                    files_to_process, len(files_to_process)
                )

            futures = {
                executor.submit(start_encode_video_file, file, args): file
                for file in files_to_process
            }
            for future in concurrent.futures.as_completed(futures):
                file = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    tb_str = traceback.format_exception(
                        etype=type(exc), value=exc, tb=exc.__traceback__
                    )
                    logger.error(
                        f"Error processing {file}: {exc}\nTraceback: {''.join(tb_str)}"
                    )

    except KeyboardInterrupt:
        logger.warning("Encoding process interrupted by user.")
    except Exception as e:
        tb_str = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        logger.error(f"An unexpected error occurred: {e}\nTraceback: {''.join(tb_str)}")

    pre_and_post_actions(process_files, path)


def start_encode_video_file(file_path: Path, args: argparse.Namespace):
    """
    Encodes a single video file.

    :param file_path: The path of the video file to encode.
    :param args: Command-line arguments containing processing configurations.
    """
    try:
        media_file = MediaFile(file_path)
        video_encoder = VideoEncoder(media_file, args)
        logger.debug(f"Starting encoding for file: {file_path}")
        video_encoder.start()
    except NoDurationFoundException:  # raised by media_file initialization
        to_dir = NO_DURATION_FOUND_ERROR_DIR / file_path.relative_to(Path.cwd())
        to_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(file_path, to_dir)
        logger.error(f"Failed to find duration: {file_path}")
    except Exception as e:
        tb_str = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        logger.error(
            f"Failed to encode file {file_path}: {e}\nTraceback: {''.join(tb_str)}"
        )
        raise


def pre_and_post_actions(process_files: ProcessVideoFiles, path: Path):
    """
    Performs cleanup and logging actions before and after processing files.

    :param process_files: The object managing the video file processes.
    :param path: The original path of the video files.
    """
    try:
        logger.debug("Performing pre & post processing actions.")
        process_files.remove_empty_dirs()
        process_files.delete_temp_folders()
        process_files.move_raw_folder_if_no_process_files(
            Path(VIDEO_OUT_DIR_ROOT).resolve()
        )
        process_files.remove_small_files()
        SuccessLog.generate_combined_log_yaml(path)
        logger.info("pre & post processing actions completed.")
    except Exception as e:
        tb_str = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        logger.error(
            f"pre & post processing actions failed: {e}\nTraceback: {''.join(tb_str)}"
        )
        raise
