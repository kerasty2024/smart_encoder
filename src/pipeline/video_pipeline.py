import concurrent.futures
import os
import argparse
import random
import shutil
import traceback
from pathlib import Path
from typing import Optional

from loguru import logger

# Domain objects
from ..domain.media import MediaFile
from ..domain.exceptions import NoDurationFoundException

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
)

# Config
from ..config.audio import TARGET_BIT_RATE_IPHONE_XR, AUDIO_ENCODED_ROOT_DIR
from ..config.video import (
    OUTPUT_DIR_IPHONE,
    VIDEO_OUT_DIR_ROOT,
    NO_DURATION_FOUND_ERROR_DIR,
)


# --- Base Pipeline Class (conceptual, if further refactoring is desired) ---
# For now, we port existing structures: EncodeStarter and stand-alone functions.


# --- Ported from encode_starter.py ---
class BaseEncodeStarter:  # Renamed from EncodeStarter for clarity if used as base
    encoder_instance: Optional[Encoder] = None  # Type hint for clarity
    process_files_handler: Optional[ProcessFiles] = None
    encoded_dir: Path  # Should be Path

    def __init__(self, project_dir: Path, args: argparse.Namespace):
        self.project_dir: Path = project_dir.resolve()  # Ensure absolute
        self.args = args
        # self.encoded_dir needs to be set by subclasses

    def process_single_file(self, path: Path):
        """Placeholder for single file processing. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement process_single_file.")

    def _initialize_file_processor(self):
        """Placeholder for initializing file processor. Subclasses must implement."""
        raise NotImplementedError(
            "Subclasses must implement _initialize_file_processor."
        )

    def process_multi_file(self):
        self._initialize_file_processor()
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                f"No source directory or files to process for {self.__class__.__name__} at {self.project_dir}"
            )
            return

        logger.info(
            f"[{self.__class__.__name__}] Remaining files: {len(self.process_files_handler.files)}"
        )

        files_to_process = list(
            self.process_files_handler.files
        )  # Make mutable for random.sample
        if self.args.random:
            files_to_process = random.sample(files_to_process, len(files_to_process))

        # Ensure processes count is at least 1
        max_workers = max(
            1, self.args.processes if hasattr(self.args, "processes") else 1
        )

        with concurrent.futures.ProcessPoolExecutor(
            max_workers=max_workers
        ) as executor:
            futures = {
                executor.submit(self.process_single_file, file_path): file_path
                for file_path in files_to_process
            }
            for future in concurrent.futures.as_completed(futures):
                file_path = futures[future]
                try:
                    future.result()  # Raise exceptions from process_single_file
                except Exception as exc:
                    tb_str = traceback.format_exception(
                        etype=type(exc), value=exc, tb=exc.__traceback__
                    )
                    logger.error(
                        f"Error processing {file_path.relative_to(self.project_dir) if self.project_dir in file_path.parents else file_path} "
                        f"in {self.__class__.__name__}: {exc}\nTraceback: {''.join(tb_str)}"
                    )
        logger.info(f"[{self.__class__.__name__}] Finished processing multiple files.")

    def common_post_actions(self):
        """Common post-actions applicable to most pipelines."""
        if self.process_files_handler:
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders()  # If applicable
        SuccessLog.generate_combined_log_yaml(self.project_dir)
        logger.info(f"[{self.__class__.__name__}] Common post-actions completed.")


class PhoneEncodingPipeline(BaseEncodeStarter):  # Renamed from PhoneEncodeStarter
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        super().__init__(project_dir, args)
        if self.args.audio_only:
            self.encoded_dir = AUDIO_ENCODED_ROOT_DIR.resolve()  # From config
        else:
            self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()  # From config

    def _initialize_file_processor(self):
        if self.args.audio_only:
            self.process_files_handler = ProcessAudioFiles(self.project_dir, self.args)
        else:
            self.process_files_handler = ProcessPhoneFiles(self.project_dir, self.args)

    def process_single_file(self, path: Path):
        logger.debug(f"PhonePipeline: Processing single file: {path}")
        try:
            media_file = MediaFile(
                path
            )  # Can raise FileNotFoundError, MediaFileException
            if self.args.audio_only:
                encoder = AudioEncoder(
                    media_file,
                    target_bit_rate=TARGET_BIT_RATE_IPHONE_XR,
                    args=self.args,
                )
            else:
                encoder = PhoneVideoEncoder(media_file, args=self.args)
            encoder.start()
        except NoDurationFoundException:
            # This specific handling was in start_encode_video_file, adapting here
            # Original PhoneEncodeStarter didn't have this explicit handling.
            # For consistency, we can add it or let it propagate to the general error logger in process_multi_file.
            # Let's assume for now it's caught by the main pool's exception handler.
            logger.error(
                f"PhonePipeline: No duration found for {path}, skipping specific move. Error will be logged by pool."
            )
            raise  # Re-raise to be caught by ProcessPoolExecutor's error handling
        except Exception as e:
            logger.error(f"PhonePipeline: Failed to process {path}: {e}")
            raise  # Re-raise to be caught

    def post_actions(self):
        """Specific post-actions for the Phone pipeline."""
        self.common_post_actions()  # Run common ones first
        if self.process_files_handler:
            # Original PhoneEncodeStarter had specific move logic
            if self.args.audio_only and hasattr(
                self.process_files_handler, "move_raw_folder_if_no_process_files"
            ):
                self.process_files_handler.move_raw_folder_if_no_process_files(
                    AUDIO_ENCODED_ROOT_DIR.resolve()
                )
            # Add other specific moves if necessary, e.g., for video to OUTPUT_DIR_IPHONE_raw
        logger.info(
            f"[{self.__class__.__name__}] Phone-specific post-actions completed."
        )


# --- Ported from start_encode_files.py (as a class for consistency) ---
class StandardVideoPipeline(BaseEncodeStarter):
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        super().__init__(project_dir, args)
        self.encoded_dir_root = VIDEO_OUT_DIR_ROOT.resolve()  # Root for encoded files

    def _initialize_file_processor(self):
        self.process_files_handler = ProcessVideoFiles(self.project_dir, self.args)

    def process_single_file(self, file_path: Path):
        """Encodes a single video file."""
        logger.debug(f"StandardPipeline: Processing single file: {file_path}")
        try:
            media_file = MediaFile(file_path)  # Can raise
            video_encoder = VideoEncoder(media_file, self.args)
            logger.debug(
                f"StandardPipeline: Starting encoding for file: {file_path.name}"
            )
            video_encoder.start()
        except NoDurationFoundException:
            logger.error(
                f"StandardPipeline: No duration found for {file_path}. Moving to error directory."
            )
            # NO_DURATION_FOUND_ERROR_DIR is absolute path from config
            # self.project_dir is the current scanning root
            # We need relative path from project_dir to file_path for constructing target error path
            try:
                relative_file_path = file_path.relative_to(self.project_dir)
                to_dir = NO_DURATION_FOUND_ERROR_DIR / relative_file_path.parent
            except ValueError:  # If file_path is not under self.project_dir (e.g. absolute path passed directly)
                to_dir = NO_DURATION_FOUND_ERROR_DIR / file_path.parent.name

            to_dir.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(file_path), to_dir / file_path.name)
                logger.info(f"Moved {file_path.name} to {to_dir} due to no duration.")
            except Exception as move_err:
                logger.error(
                    f"Could not move {file_path.name} to error dir: {move_err}"
                )
            # This exception is now "handled" by moving. It should not be re-raised to stop the pool for other files.
        except Exception as e:
            logger.error(
                f"StandardPipeline: Failed to encode file {file_path.name}: {e}"
            )
            # Re-raise to be caught by ProcessPoolExecutor's error handling for logging
            raise

    def _perform_file_management_actions(self):
        """Gathers file management actions that were in 'pre_and_post_actions'."""
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.warning(
                "StandardPipeline: File processor not initialized, skipping file management."
            )
            return
        try:
            logger.debug("StandardPipeline: Performing file management actions.")
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders()
            if hasattr(
                self.process_files_handler, "move_raw_folder_if_no_process_files"
            ):
                self.process_files_handler.move_raw_folder_if_no_process_files(
                    self.encoded_dir_root
                )
            # self.process_files_handler.remove_small_files() # Uncomment if needed
            SuccessLog.generate_combined_log_yaml(self.project_dir)  # Log generation
            logger.info("StandardPipeline: File management actions completed.")
        except Exception as e:
            tb_str = traceback.format_exception(
                etype=type(e), value=e, tb=e.__traceback__
            )
            logger.error(
                f"StandardPipeline: File management actions failed: {e}\nTraceback: {''.join(tb_str)}"
            )
            # Decide if this should halt further operations or just log
            # raise

    def run(self):
        """Main execution flow for standard video processing."""
        logger.debug(
            f"StandardPipeline: Starting video encoding in path: {self.project_dir}"
        )

        # Initialize file processor early to use for pre-actions
        self._initialize_file_processor()
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                "StandardPipeline: No source directory found after init, exiting."
            )
            return

        self._perform_file_management_actions()  # Initial cleanup / log generation

        # Check again if source_dir is still valid after potential cleanup
        if not self.process_files_handler.source_dir:
            logger.info(
                "StandardPipeline: No source directory after pre-actions, exiting."
            )
            return

        try:
            self.process_multi_file()  # This contains the ProcessPoolExecutor logic
        except KeyboardInterrupt:
            logger.warning("StandardPipeline: Encoding process interrupted by user.")
        except Exception as e:  # Catch unexpected errors from process_multi_file itself
            tb_str = traceback.format_exception(
                etype=type(e), value=e, tb=e.__traceback__
            )
            logger.error(
                f"StandardPipeline: An unexpected error occurred during multi-file processing: {e}\nTraceback: {''.join(tb_str)}"
            )
        finally:
            self._perform_file_management_actions() # Final cleanup / log generation
            logger.info("StandardPipeline: Processing run finished.")