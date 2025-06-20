"""
Defines the encoding pipelines that orchestrate the video and audio processing workflows.

This module contains the core logic for managing the encoding process from start to
finish. It defines a base pipeline class (`BaseEncodeStarter`) and specific
implementations for different encoding scenarios:

- `PhoneEncodingPipeline`: A specialized pipeline for encoding media files (video or
  audio-only) with settings optimized for mobile devices, particularly iPhones.
- `StandardVideoPipeline`: The main pipeline for general-purpose video encoding,
  which includes an automated quality-finding step (CRF search) before the
  main encode.

These pipelines handle file discovery, parallel processing using a process pool,
job state management (via `EncodeInfo`), and post-processing cleanup actions.
"""

import concurrent.futures
import os
import argparse
import random
import shutil
import traceback
from pathlib import Path
from typing import Optional, List, Any
import sys

from loguru import logger

# Domain objects
from ..domain.media import MediaFile
from ..domain.exceptions import NoDurationFoundException, MediaFileException
from ..domain.temp_models import EncodeInfo

# Services
from ..services.encoding_service import (
    Encoder,
    PhoneVideoEncoder,
    AudioEncoder,
    VideoEncoder,
)
from ..services.logging_service import SuccessLog
from ..services.file_processing_service import (
    ProcessFiles,
    ProcessPhoneFiles,
    ProcessAudioFiles,
    ProcessVideoFiles,
    cleanup_empty_error_dirs,
)

# Config
from ..config.audio import (
    TARGET_BIT_RATE_IPHONE_XR,
    AUDIO_ENCODED_ROOT_DIR,
    AUDIO_ENCODED_RAW_DIR,
)
from ..config.video import (
    OUTPUT_DIR_IPHONE,
    VIDEO_OUT_DIR_ROOT,
    NO_DURATION_FOUND_ERROR_DIR,
    COMPLETED_RAW_DIR,
)
from ..config.common import (
    MAX_ENCODE_RETRIES,
    JOB_STATUS_PENDING,
    JOB_STATUS_PREPROCESSING_STARTED,
    JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR_RETRYABLE,
    JOB_STATUS_ERROR_PERMANENT,
    JOB_STATUS_SKIPPED,
)


class BaseEncodeStarter:
    """
    An abstract base class for encoding pipelines.

    This class provides the foundational structure and common functionalities for all
    encoding pipelines, such as initializing paths, managing concurrent processing of
    files, and performing common cleanup actions. Subclasses are expected to implement
    the specifics of their encoding strategy.

    Attributes:
        encoder_instance (Optional[Encoder]): The encoder service instance. To be defined by subclasses.
        process_files_handler (Optional[ProcessFiles]): The file processing service instance.
        encoded_dir (Path): The root directory for encoded output.
        project_dir (Path): The absolute path to the directory being processed.
        args (argparse.Namespace): The command-line arguments.
    """
    encoder_instance: Optional[Encoder] = None
    process_files_handler: Optional[ProcessFiles] = None
    encoded_dir: Path

    def __init__(self, project_dir: Path, args: argparse.Namespace):
        """
        Initializes the BaseEncodeStarter.

        Args:
            project_dir: The directory where media files are located.
            args: The parsed command-line arguments.
        """
        self.project_dir: Path = project_dir.resolve()
        self.args = args

    def process_single_file(self, path: Path):
        """
        A placeholder method for processing a single media file.

        Subclasses must implement this method to define the specific encoding
        logic for a single file. This method will be called concurrently by
        `process_multi_file`.

        Args:
            path: The path to the media file to process.
        """
        raise NotImplementedError("Subclasses must implement process_single_file.")

    def _initialize_file_processor(self):
        """

        A placeholder method for initializing the file processor.

        Subclasses must implement this method to instantiate the appropriate
        `ProcessFiles` handler (e.g., `ProcessVideoFiles`, `ProcessAudioFiles`)
        which will discover the files to be processed.
        """
        raise NotImplementedError(
            "Subclasses must implement _initialize_file_processor."
        )

    def process_multi_file(self):
        """
        Processes multiple media files in parallel.

        This method orchestrates the main batch processing loop. It does the following:
        1. Initializes the file processor to get a list of files.
        2. Sorts or randomizes the file list based on command-line arguments.
        3. Creates a process pool using `concurrent.futures.ProcessPoolExecutor`.
        4. Submits each file to the `process_single_file` method for concurrent execution.
        5. Waits for all tasks to complete and logs any errors that occur in the child processes.
        """
        self._initialize_file_processor()
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                f"No source directory or files to process for {self.__class__.__name__} at {self.project_dir}"
            )
            return

        files_to_process_paths: List[Path] = list(self.process_files_handler.files)
        logger.debug(
            f"[{self.__class__.__name__}] Initial file count: {len(files_to_process_paths)}"
        )

        if self.args.random:
            logger.debug(f"[{self.__class__.__name__}] Randomizing file order.")
            files_to_process_paths = random.sample(
                files_to_process_paths, len(files_to_process_paths)
            )
        else:
            files_to_process_paths.sort()

        if not files_to_process_paths:
            logger.info(
                f"[{self.__class__.__name__}] No files left to process after filtering/prioritization."
            )
            return

        logger.info(
            f"[{self.__class__.__name__}] Final files to process in this batch: {len(files_to_process_paths)}"
        )
        for i, f_path in enumerate(files_to_process_paths):
            logger.trace(f"  {i+1}. {f_path.name}")

        max_workers = max(
            1, self.args.processes if hasattr(self.args, "processes") else 1
        )
        logger.info(
            f"[{self.__class__.__name__}] Using {max_workers} worker process(es)."
        )

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers
        ) as executor:
            futures = {
                executor.submit(self.process_single_file, file_path): file_path
                for file_path in files_to_process_paths
            }
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                file_path = futures[future]
                try:
                    logger.debug(
                        f"Waiting for result from file {i+1}/{len(files_to_process_paths)}: {file_path.name}"
                    )
                    future.result()
                    logger.debug(f"Successfully completed task for: {file_path.name}")
                except (
                    Exception
                ) as exc:  # This catches exceptions from the child process
                    if sys.version_info >= (3, 10):
                        tb_str = traceback.format_exception(exc)
                    else:
                        tb_str = traceback.format_exception(
                            type(exc), exc, exc.__traceback__
                        )
                    logger.error(
                        f"Error processing task for {file_path.name} in pool (main process view):\n"
                        f"Exception type: {type(exc).__name__}\n"
                        f"Exception message: {exc}\n"
                        f"Traceback: {''.join(tb_str)}"
                    )
                    # Child process should have logged its own detailed error and updated EncodeInfo.
                    # No direct access to child's EncodeInfo object here.
        logger.info(
            f"[{self.__class__.__name__}] Finished processing all files in this batch."
        )

    def common_post_actions(self):
        """
        Performs common cleanup and finalization tasks after processing.

        This includes removing empty directories, deleting temporary folders, and
        generating a consolidated success log from individual log files.
        """
        if self.process_files_handler:
            logger.info(
                f"[{self.__class__.__name__}] Running common post-actions: removing empty dirs, deleting temp folders."
            )
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders()
        logger.info(f"[{self.__class__.__name__}] Generating combined success log.")
        SuccessLog.generate_combined_log_yaml(self.project_dir)
        logger.info(f"[{self.__class__.__name__}] Common post-actions completed.")


class PhoneEncodingPipeline(BaseEncodeStarter):
    """
    A pipeline specifically for encoding media for mobile devices (e.g., iPhone).

    This pipeline handles both video and audio-only encoding tasks using profiles
    optimized for mobile playback. It uses fixed encoding parameters rather than
    performing a quality search, making it faster for this specific use case.
    """
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        """
        Initializes the PhoneEncodingPipeline.

        Sets the output directory based on whether it's an audio-only or a
        video encoding task.

        Args:
            project_dir: The directory containing media files.
            args: The parsed command-line arguments.
        """
        super().__init__(project_dir, args)
        if self.args.audio_only:
            self.encoded_dir = AUDIO_ENCODED_ROOT_DIR.resolve()
        else:
            self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

    def _initialize_file_processor(self):
        """
        Initializes the file processor for the phone pipeline.

        It selects `ProcessAudioFiles` for audio-only tasks or `ProcessPhoneFiles`
        for video tasks, based on the `--audio-only` flag.
        """
        if self.args.audio_only:
            self.process_files_handler = ProcessAudioFiles(self.project_dir, self.args)
        else:
            self.process_files_handler = ProcessPhoneFiles(self.project_dir, self.args)

    def process_single_file(self, path: Path):
        """
        Processes a single media file for the phone-specific profile.

        This method is the entry point for each concurrent worker process. It:
        1. Instantiates a `MediaFile` to analyze the source file.
        2. Selects the appropriate encoder (`AudioEncoder` or `PhoneVideoEncoder`).
        3. Checks the `EncodeInfo` for the job's status to handle resumes, skips, or retries.
        4. Calls the encoder's `start()` method to begin the actual encoding.
        5. Handles exceptions, logs errors, and updates the job status accordingly.

        Args:
            path: The path to the media file to be processed.
        """
        logger.debug(f"PhonePipeline: Child process started for: {path.name}")
        media_file: Optional[MediaFile] = None
        encoder_instance: Optional[Encoder] = None
        job_info_path_for_log: Optional[Path] = None

        try:
            media_file = MediaFile(path)
            if self.args.audio_only:
                encoder_instance = AudioEncoder(
                    media_file,
                    target_bit_rate=TARGET_BIT_RATE_IPHONE_XR,
                    args=self.args,
                )
            else:
                encoder_instance = PhoneVideoEncoder(media_file, args=self.args)

            job_info = encoder_instance.encode_info
            job_info_path_for_log = job_info.path  # For logging in case of error

            if job_info.status == JOB_STATUS_COMPLETED:
                logger.info(
                    f"File {path.name} (Phone) is already completed. Skipping encode, running post_actions."
                )
                encoder_instance.post_actions()
                return
            # ... (other status checks: SKIPPED, ERROR_PERMANENT, MAX_RETRIES) ...
            if job_info.status == JOB_STATUS_SKIPPED:
                logger.info(
                    f"File {path.name} (Phone) was previously skipped. Skipping encode, running post_actions."
                )
                encoder_instance.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_PERMANENT:
                logger.warning(
                    f"File {path.name} (Phone) has a permanent error. Skipping encode, running post_actions."
                )
                encoder_instance.post_actions()
                return
            if (
                job_info.status == JOB_STATUS_ERROR_RETRYABLE
                and job_info.retry_count >= MAX_ENCODE_RETRIES
            ):
                logger.error(
                    f"File {path.name} (Phone) reached max retries ({MAX_ENCODE_RETRIES}). Marking as permanent."
                )
                job_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message="Max retries reached.",
                )
                encoder_instance.post_actions()
                return

            logger.info(
                f"Starting/Resuming PhonePipeline task for {path.name} (Status: {job_info.status})"
            )
            encoder_instance.start()
            logger.debug(
                f"PhonePipeline: Child process finished successfully for: {path.name}"
            )

        except (NoDurationFoundException, MediaFileException) as media_err:
            logger.error(
                f"PhonePipeline: MediaFile initialization error for {path.name}: {media_err}"
            )
            # MediaFile itself handles moving to load_failed. No EncodeInfo to update if md5 unknown.
            # Re-raise so pool executor logs it.
            raise
        except Exception as e:
            # This is a catch-all for any other unhandled exception within this task.
            # Log detailed error information including the file being processed.
            if sys.version_info >= (3, 10):
                tb_str = traceback.format_exception(e)
            else:
                tb_str = traceback.format_exception(type(e), e, e.__traceback__)

            error_msg = (
                f"PhonePipeline: Unhandled error in child process for file: {path.name}\n"
                f"Exception type: {type(e).__name__}\n"
f"Exception message: {e}\n"
                f"EncodeInfo path (if available): {job_info_path_for_log}\n"
                f"Traceback:\n{''.join(tb_str)}"
            )
            logger.error(error_msg)

            if encoder_instance and encoder_instance.encode_info:
                encoder_instance.encode_info.dump(
                    status=JOB_STATUS_ERROR_RETRYABLE,
                    last_error_message=f"Unhandled Pipeline Error: {e}",
                    increment_retry_count=True,
                )
                try:
                    encoder_instance.post_actions()  # Attempt to run post_actions even on error
                except Exception as post_err:
                    logger.error(
                        f"Error during post_actions after unhandled error for {path.name}: {post_err}"
                    )
            # Re-raise the original exception to be caught by ProcessPoolExecutor's main loop
            raise
        finally:
            logger.debug(f"PhonePipeline: Child process for {path.name} is exiting.")

    def post_actions(self):
        """
        Performs post-processing actions specific to the phone pipeline.

        This calls the common post-actions and adds specific logic to handle the
        archiving of empty raw material folders.
        """
        self.common_post_actions()
        if self.process_files_handler:
            if self.args.audio_only and hasattr(
                self.process_files_handler, "move_raw_folder_if_no_process_files"
            ):
                logger.info(
                    f"[{self.__class__.__name__}] Checking for empty raw audio folders to move."
                )
                self.process_files_handler.move_raw_folder_if_no_process_files(
                    AUDIO_ENCODED_RAW_DIR
                )
            elif not self.args.audio_only and hasattr(
                self.process_files_handler, "move_raw_folder_if_no_process_files"
            ):
                logger.info(
                    f"[{self.__class__.__name__}] Checking for empty raw phone video folders to move."
                )
                phone_video_raw_target_root = COMPLETED_RAW_DIR / "phone_encoded_raw"
                self.process_files_handler.move_raw_folder_if_no_process_files(
                    phone_video_raw_target_root
                )

        try:
            logger.info(
                f"[{self.__class__.__name__}] Final cleanup of empty error directories."
            )
            cleanup_empty_error_dirs(self.project_dir)
        except Exception as e:
            logger.error(f"An error occurred during final error directory cleanup: {e}")

        logger.info(
            f"[{self.__class__.__name__}] Phone-specific post-actions completed."
        )


class StandardVideoPipeline(BaseEncodeStarter):
    """
    The main pipeline for standard, high-quality video encoding.

    This pipeline is responsible for processing general video files. Its key feature
    is a two-stage process: first, a pre-encoding step (`PreVideoEncoder`) determines
    the optimal quality settings (CRF) for a target VMAF score. Second, the main
    `VideoEncoder` uses these settings to perform the final, full-length encode.
    """
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        """
        Initializes the StandardVideoPipeline.

        Args:
            project_dir: The directory containing video files.
            args: The parsed command-line arguments.
        """
        super().__init__(project_dir, args)
        self.encoded_dir = VIDEO_OUT_DIR_ROOT.resolve()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

    def _initialize_file_processor(self):
        """
        Initializes the file processor to discover video files.

        This implementation uses `ProcessVideoFiles` to scan for and list all
        supported video file types in the target directory.
        """
        self.process_files_handler = ProcessVideoFiles(self.project_dir, self.args)

    def process_single_file(self, file_path: Path):
        """
        Processes a single video file through the standard pipeline.

        This is the core logic for each worker process. It performs the following steps:
        1. Instantiates `MediaFile` to analyze the video.
        2. Instantiates `VideoEncoder`, which in turn creates a `PreVideoEncoder`.
        3. Checks the `EncodeInfo` for job status to handle resumes, skips, or retries.
        4. Calls `video_encoder.start()`, which triggers the pre-encoding (CRF search)
           and then the main encoding process.
        5. Handles exceptions, logs errors, and manages job state.

        Args:
            file_path: The path to the video file to be processed.
        """
        logger.debug(f"StandardPipeline: Child process started for: {file_path.name}")
        media_file: Optional[MediaFile] = None
        video_encoder: Optional[VideoEncoder] = None
        job_info_path_for_log: Optional[Path] = None

        try:
            logger.debug(f"Initializing MediaFile for {file_path.name}")
            media_file = MediaFile(file_path)

            logger.debug(f"Initializing VideoEncoder for {file_path.name}")
            video_encoder = VideoEncoder(media_file, self.args)
            job_info = video_encoder.encode_info
            job_info_path_for_log = job_info.path

            logger.debug(f"Checking job status for {file_path.name}: {job_info.status}")
            if job_info.status == JOB_STATUS_COMPLETED:
                logger.info(
                    f"File {file_path.name} (Standard) is already completed. Skipping encode, running post_actions."
                )
                video_encoder.post_actions()
                return
            if job_info.status == JOB_STATUS_SKIPPED:
                logger.info(
                    f"File {file_path.name} (Standard) was previously skipped. Skipping encode, running post_actions."
                )
                video_encoder.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_PERMANENT:
                logger.warning(
                    f"File {file_path.name} (Standard) has a permanent error. Skipping encode, running post_actions."
                )
                video_encoder.post_actions()
                return
            if (
                job_info.status == JOB_STATUS_ERROR_RETRYABLE
                and job_info.retry_count >= MAX_ENCODE_RETRIES
            ):
                logger.error(
                    f"File {file_path.name} (Standard) reached max retries ({MAX_ENCODE_RETRIES}). Marking as permanent."
                )
                job_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message="Max retries reached.",
                )
                video_encoder.post_actions()
                return

            logger.info(
                f"Starting/Resuming StandardPipeline task for {file_path.name} (Status: {job_info.status})"
            )
            video_encoder.start()
            logger.debug(
                f"StandardPipeline: Child process finished successfully for: {file_path.name}"
            )

        except (NoDurationFoundException, MediaFileException) as media_err:
            logger.error(
                f"StandardPipeline: MediaFile initialization error for {file_path.name}: {media_err}"
            )
            if (
                media_file and hasattr(media_file, "md5") and media_file.md5
            ):  # If MD5 was obtained
                temp_storage_dir = (
                    self.encoded_dir / media_file.relative_dir / ".encode_info_cache"
                )
                temp_storage_dir.mkdir(parents=True, exist_ok=True)
                temp_job_info = EncodeInfo(media_file.md5, storage_dir=temp_storage_dir)
                temp_job_info.load()  # Load if exists
                temp_job_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message=f"MediaFile Error: {media_err}",
                )
            # MediaFile.handle_load_failure should have moved the file if probing failed.
            # If duration was not found after successful probe, move it here.
            if (
                isinstance(media_err, NoDurationFoundException)
                and media_file
                and media_file.path.exists()
            ):
                error_dest_dir_base = NO_DURATION_FOUND_ERROR_DIR
                target_error_subdir = error_dest_dir_base / media_file.relative_dir
                target_error_subdir.mkdir(parents=True, exist_ok=True)
                final_error_path = target_error_subdir / media_file.filename
                if media_file.path.resolve() != final_error_path.resolve():
                    try:
                        shutil.move(str(media_file.path), str(final_error_path))
                        logger.info(
                            f"Moved {media_file.filename} to {final_error_path} due to no duration."
                        )
                    except Exception as move_err:
                        logger.error(
                            f"Could not move {media_file.filename} to error dir {final_error_path}: {move_err}"
                        )
            raise  # Re-raise for pool executor
        except Exception as e:
            if sys.version_info >= (3, 10):
                tb_str = traceback.format_exception(e)
            else:
                tb_str = traceback.format_exception(type(e), e, e.__traceback__)
            error_msg = (
                f"StandardPipeline: Unhandled error in child process for file: {file_path.name}\n"
                f"Exception type: {type(e).__name__}\n"
                f"Exception message: {e}\n"
                f"EncodeInfo path (if available): {job_info_path_for_log}\n"
                f"Traceback:\n{''.join(tb_str)}"
            )
            logger.error(error_msg)
            if video_encoder and video_encoder.encode_info:
                video_encoder.encode_info.dump(
                    status=JOB_STATUS_ERROR_RETRYABLE,
                    last_error_message=f"Unhandled Pipeline Error: {e}",
                    increment_retry_count=True,
                )
                try:
                    video_encoder.post_actions()
                except Exception as post_err:
                    logger.error(
                        f"Error during post_actions after unhandled error for {file_path.name}: {post_err}"
                    )
            raise
        finally:
            logger.debug(
                f"StandardPipeline: Child process for {file_path.name} is exiting."
            )

    def _perform_file_management_actions(self, is_final_run: bool = False):
        """
        Encapsulates file and directory cleanup tasks.

        This method removes empty directories and temporary folders. On the final
        run of the pipeline, it performs more thorough cleanup, such as removing
        empty error directories.

        Args:
            is_final_run: If True, performs additional final cleanup tasks.
        """
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.warning(
                "StandardPipeline: File processor not initialized, skipping file management."
            )
            return
        try:
            logger.debug("StandardPipeline: Performing file management actions.")
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders()

            if is_final_run:
                if hasattr(
                    self.process_files_handler, "move_raw_folder_if_no_process_files"
                ):
                    logger.info(
                        f"[{self.__class__.__name__}] Checking for empty raw video folders to move (final run)."
                    )
                    self.process_files_handler.move_raw_folder_if_no_process_files(
                        COMPLETED_RAW_DIR
                    )

                # Perform error directory cleanup only on the final run
                try:
                    logger.info(
                        f"[{self.__class__.__name__}] Final cleanup of empty error directories."
                    )
                    cleanup_empty_error_dirs(self.project_dir)
                except Exception as e:
                    logger.error(
                        f"An error occurred during final error directory cleanup: {e}"
                    )

            SuccessLog.generate_combined_log_yaml(self.project_dir)
            logger.info("StandardPipeline: File management actions completed.")
        except Exception as e:
            if sys.version_info >= (3, 10):
                tb_str = traceback.format_exception(e)
            else:
                tb_str = traceback.format_exception(type(e), e, e.__traceback__)
            logger.error(
                f"StandardPipeline: File management actions failed: {e}\nTraceback: {''.join(tb_str)}"
            )

    def run(self):
        """
        The main entry point for the standard video pipeline.

        This method orchestrates the entire workflow:
        1. Initializes the file processor.
        2. Performs preliminary file management actions.
        3. Calls `process_multi_file` to execute the main encoding loop.
        4. Handles graceful shutdown on user interruption (`KeyboardInterrupt`).
        5. Performs final file management and cleanup actions.
        """
        logger.info(  # Changed to INFO
            f"StandardPipeline: Starting video encoding in path: {self.project_dir}"
        )
        self._initialize_file_processor()
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                "StandardPipeline: No source directory found after init, exiting."
            )
            return
        self._perform_file_management_actions(is_final_run=False)
        if not self.process_files_handler.source_dir:
            logger.info(
                "StandardPipeline: No source directory after pre-actions, exiting."
            )
            return
        try:
            self.process_multi_file()
        except KeyboardInterrupt:
            logger.warning(
                "StandardPipeline: Encoding process interrupted by user. Will attempt final cleanup."
            )
        except Exception as e:
            if sys.version_info >= (3, 10):
                tb_str = traceback.format_exception(e)
            else:
                tb_str = traceback.format_exception(type(e), e, e.__traceback__)
            logger.error(
                f"StandardPipeline: An unexpected error occurred during multi-file processing: {e}\nTraceback: {''.join(tb_str)}"
            )
        finally:
            logger.info("StandardPipeline: Starting final file management actions...")
            self._perform_file_management_actions(is_final_run=True)
            logger.info("StandardPipeline: Processing run finished.")