import argparse
import concurrent.futures
import random
import traceback
from pathlib import Path

from loguru import logger

from scripts.controllers.functions import format_timedelta, formatted_size
from scripts.models.Encoder import VideoEncoder
from scripts.models.Log import SuccessLog
from scripts.models.MediaFile import MediaFile
from scripts.models.ProcessFiles import ProcessVideoFiles
from scripts.settings.video import VIDEO_OUT_DIR_ROOT


def start_encode_video_files_multi_process(path: Path, args: argparse.Namespace = None):
    """
    Encodes multiple video files concurrently using a process pool.

    :param path: Directory path containing video files to be processed.
    :param args: Command-line arguments containing processing configurations.
    """
    logger.debug(f"Starting video encoding in path: {path}")
    process_files = ProcessVideoFiles(path, args)

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

    post_actions(process_files, path)


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
        logger.success(
            f"encoded {file_path}, "
            f"duration: {format_timedelta(video_encoder.total_time)}, "
            f"encoded size: {formatted_size(video_encoder.encoded_file.stat().st_size)}"
            f"({int(video_encoder.encoded_file.stat().st_size / video_encoder.pre_encoder.media_file.size) * 100}%)"
        )
    except Exception as e:
        tb_str = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        logger.error(
            f"Failed to encode file {file_path}: {e}\nTraceback: {''.join(tb_str)}"
        )
        raise


def post_actions(process_files: ProcessVideoFiles, path: Path):
    """
    Performs cleanup and logging actions after processing files.

    :param process_files: The object managing the video file processes.
    :param path: The original path of the video files.
    """
    try:
        logger.debug("Performing post-processing actions.")
        process_files.remove_empty_dirs()
        process_files.delete_temp_folders()
        process_files.move_raw_folder_if_no_process_files(
            Path(VIDEO_OUT_DIR_ROOT).resolve()
        )
        SuccessLog.generate_combined_log_yaml(path)
        logger.info("Post-processing actions completed.")
    except Exception as e:
        tb_str = traceback.format_exception(etype=type(e), value=e, tb=e.__traceback__)
        logger.error(f"Post-processing failed: {e}\nTraceback: {''.join(tb_str)}")
        raise
