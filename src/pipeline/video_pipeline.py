import concurrent.futures
import os # Keep os for general path ops if any, but Path is preferred
import argparse # Keep for type hint
import random
import shutil
import traceback
from pathlib import Path
from typing import Optional, List, Tuple, Any # Keep Any for args
import sys # sysモジュールをインポート

from loguru import logger

# Domain objects
from ..domain.media import MediaFile
from ..domain.exceptions import NoDurationFoundException #, MediaFileException (if needed for specific handling)
from ..domain.temp_models import EncodeInfo # For progress tracking

# Services
from ..services.encoding_service import (
    Encoder, # Base class for type hints
    PhoneVideoEncoder,
    AudioEncoder,
    VideoEncoder,
)
from ..services.logging_service import SuccessLog
from ..services.file_processing_service import (
    ProcessFiles, # Base class
    ProcessPhoneFiles,
    ProcessAudioFiles,
    ProcessVideoFiles,
)

# Config
from ..config.audio import TARGET_BIT_RATE_IPHONE_XR, AUDIO_ENCODED_ROOT_DIR, AUDIO_ENCODED_RAW_DIR
from ..config.video import (
    OUTPUT_DIR_IPHONE,
    VIDEO_OUT_DIR_ROOT,
    NO_DURATION_FOUND_ERROR_DIR,
    COMPLETED_RAW_DIR # COMPLETED_RAW_DIR をインポート
)
from ..config.common import ( # Import job status constants and MAX_RETRIES
    MAX_ENCODE_RETRIES,
    JOB_STATUS_PENDING, JOB_STATUS_PREPROCESSING_STARTED, JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE, JOB_STATUS_ENCODING_FFMPEG_STARTED, JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR_RETRYABLE, JOB_STATUS_ERROR_PERMANENT, JOB_STATUS_SKIPPED
)


class BaseEncodeStarter:
    encoder_instance: Optional[Encoder] = None # Type hint for the specific encoder used by subclass
    process_files_handler: Optional[ProcessFiles] = None
    encoded_dir: Path  # Base output directory, set by subclasses

    def __init__(self, project_dir: Path, args: argparse.Namespace):
        self.project_dir: Path = project_dir.resolve()
        self.args = args
        # self.encoded_dir must be set by subclass __init__

    def process_single_file(self, path: Path):
        """Placeholder for single file processing. Subclasses must implement."""
        raise NotImplementedError("Subclasses must implement process_single_file.")

    def _initialize_file_processor(self):
        """Placeholder for initializing file processor. Subclasses must implement."""
        raise NotImplementedError(
            "Subclasses must implement _initialize_file_processor."
        )

    def _get_job_info_for_file(self, file_path: Path, media_file: MediaFile) -> EncodeInfo:
        """
        Helper to get or create EncodeInfo for a file.
        This needs to know where EncodeInfo files are stored, which depends on the Encoder type.
        This is a simplified helper; actual storage path logic is in Encoder/PreEncoder.
        """
        # Determine storage dir based on encoder type / output conventions.
        # This is a bit of a chicken-and-egg problem if called before encoder is chosen.
        # Assuming a common ".encode_info_cache" within the *final* output dir for that file.
        # This example uses a generic cache location if encoded_dir is not specific enough.
        # More robust: Pass the specific encoder's output dir logic.

        # For VideoEncoder: VIDEO_OUT_DIR_ROOT / media_file.relative_dir / ".encode_info_cache"
        # For AudioEncoder: AUDIO_ENCODED_ROOT_DIR / media_file.relative_dir / ".encode_info_cache"
        # For PhoneVideoEncoder: Path(OUTPUT_DIR_IPHONE) / ".encode_info_cache" (no relative_dir in example)

        # This helper is tricky to make generic here.
        # The Encoder.__init__ for each type handles its EncodeInfo creation/loading.
        # So, we create the MediaFile, then the specific Encoder, then access encoder.encode_info.
        # This function might be better placed or used differently.
        # For now, let process_single_file handle EncodeInfo via Encoder instance.

        # This is a placeholder to illustrate where EncodeInfo would be fetched if prioritizing:
        default_storage_path = self.encoded_dir / media_file.relative_dir / ".encode_info_cache"
        if isinstance(self, PhoneEncodingPipeline) and self.args.audio_only:
            default_storage_path = AUDIO_ENCODED_ROOT_DIR / media_file.relative_dir / ".encode_info_cache"
        elif isinstance(self, PhoneEncodingPipeline) and not self.args.audio_only:
            default_storage_path = Path(OUTPUT_DIR_IPHONE).resolve() / media_file.relative_dir / ".encode_info_cache" # Phone video

        default_storage_path.mkdir(parents=True, exist_ok=True)
        encode_info = EncodeInfo(media_file.md5, storage_dir=default_storage_path)
        encode_info.load()
        return encode_info


    def process_multi_file(self):
        self._initialize_file_processor()
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                f"No source directory or files to process for {self.__class__.__name__} at {self.project_dir}"
            )
            return

        # --- Prioritize files based on EncodeInfo status ---
        files_to_process_paths: List[Path] = list(self.process_files_handler.files)

        prioritized_files: List[Path] = []
        pending_new_files: List[Path] = []

        # This prioritization requires MediaFile creation and EncodeInfo loading for each file *before* processing.
        # This can be slow. A lighter way is needed or accept no prioritization beyond random.
        # For now, keep it simple: shuffle all if random, otherwise process in discovered order.
        # Status checks will happen *inside* process_single_file.

        logger.debug(
            f"[{self.__class__.__name__}] Initial file count: {len(files_to_process_paths)}"
        )

        if self.args.random: # Simple shuffle of all files
            logger.debug(f"[{self.__class__.__name__}] Randomizing file order.")
            files_to_process_paths = random.sample(files_to_process_paths, len(files_to_process_paths))
        else: # Sort by path for deterministic order (optional)
            files_to_process_paths.sort()

        if not files_to_process_paths:
            logger.info(f"[{self.__class__.__name__}] No files left to process after filtering/prioritization.")
            return

        logger.info(
            f"[{self.__class__.__name__}] Final files to process in this batch: {len(files_to_process_paths)}"
        )
        for i, f_path in enumerate(files_to_process_paths):
            logger.trace(f"  {i+1}. {f_path.name}")


        max_workers = max(
            1, self.args.processes if hasattr(self.args, "processes") else 1
        )
        logger.info(f"[{self.__class__.__name__}] Using {max_workers} worker process(es).")

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
                    logger.debug(f"Waiting for result from file {i+1}/{len(files_to_process_paths)}: {file_path.name}")
                    future.result()  # Raise exceptions from process_single_file
                    logger.debug(f"Completed processing for: {file_path.name}")
                except Exception as exc:
                    # Errors should be handled within process_single_file and EncodeInfo updated.
                    # This catch is for unexpected errors from the process_single_file *itself*
                    # or if an error wasn't caught and re-raised.
                    if sys.version_info >= (3, 10):
                        tb_str = traceback.format_exception(exc)
                    else:
                        tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)
                    logger.error(
                        f"Critical error processing {file_path.relative_to(self.project_dir) if self.project_dir in file_path.parents else file_path} "
                        f"in {self.__class__.__name__} (Pool Level): {exc}\nTraceback: {''.join(tb_str)}"
                    )
                    # Mark this file's EncodeInfo as permanently failed if possible,
                    # but MediaFile/EncodeInfo objects are in the child process.
                    # The child process should have updated EncodeInfo.
        logger.info(f"[{self.__class__.__name__}] Finished processing all files in this batch.")


    def common_post_actions(self):
        """Common post-actions applicable to most pipelines."""
        if self.process_files_handler:
            logger.info(f"[{self.__class__.__name__}] Running common post-actions: removing empty dirs, deleting temp folders.")
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders() # If applicable

        logger.info(f"[{self.__class__.__name__}] Generating combined success log.")
        SuccessLog.generate_combined_log_yaml(self.project_dir) # Consolidate all success logs
        logger.info(f"[{self.__class__.__name__}] Common post-actions completed.")


class PhoneEncodingPipeline(BaseEncodeStarter):
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        super().__init__(project_dir, args)
        # Determine base output directory based on audio_only flag
        if self.args.audio_only:
            self.encoded_dir = AUDIO_ENCODED_ROOT_DIR.resolve()
        else:
            self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)


    def _initialize_file_processor(self):
        if self.args.audio_only:
            self.process_files_handler = ProcessAudioFiles(self.project_dir, self.args)
        else:
            self.process_files_handler = ProcessPhoneFiles(self.project_dir, self.args)


    def process_single_file(self, path: Path):
        logger.debug(f"PhonePipeline: Processing single file: {path}")
        media_file: Optional[MediaFile] = None
        encoder_instance: Optional[Encoder] = None # To hold AudioEncoder or PhoneVideoEncoder

        try:
            media_file = MediaFile(path) # Can raise FileNotFoundError, MediaFileException

            # Encoder instance creation (this also initializes/loads EncodeInfo within the encoder)
            if self.args.audio_only:
                encoder_instance = AudioEncoder(
                    media_file,
                    target_bit_rate=TARGET_BIT_RATE_IPHONE_XR, # bps
                    args=self.args,
                )
            else:
                encoder_instance = PhoneVideoEncoder(media_file, args=self.args)

            # --- Check EncodeInfo status via encoder_instance.encode_info ---
            job_info = encoder_instance.encode_info
            if job_info.status == JOB_STATUS_COMPLETED:
                logger.info(f"File {path.name} (Phone) is already completed. Finalizing actions.")
                encoder_instance.post_actions() # For logging, raw file move
                return
            if job_info.status == JOB_STATUS_SKIPPED:
                logger.info(f"File {path.name} (Phone) was previously skipped. Finalizing actions.")
                encoder_instance.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_PERMANENT:
                logger.warning(f"File {path.name} (Phone) has a permanent error. Skipping.")
                encoder_instance.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_RETRYABLE and job_info.retry_count >= MAX_ENCODE_RETRIES:
                logger.error(f"File {path.name} (Phone) reached max retries ({MAX_ENCODE_RETRIES}). Marking as permanent.")
                job_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Max retries reached.")
                encoder_instance.post_actions() # Handles potential error state logging/cleanup
                return

            # --- If not returned, proceed with encoding ---
            logger.info(f"Starting/Resuming PhonePipeline task for {path.name} (Status: {job_info.status})")
            encoder_instance.start() # This handles all encoding logic and status updates

        except NoDurationFoundException: # From MediaFile(path)
            logger.error(
                f"PhonePipeline: No duration found for {path}. This is a critical error for MediaFile init."
            )
            # If MediaFile init fails, we don't have md5 for EncodeInfo easily.
            # Move to a generic load_failed dir is handled by MediaFile itself.
            # This exception should ideally be caught by the pool if re-raised.
            # For now, log and let it propagate to pool's error handler.
            raise
        except Exception as e: # Catch-all for other errors in this scope
            logger.error(f"PhonePipeline: Unhandled error processing {path}: {e}", exc_info=True)
            if encoder_instance and encoder_instance.encode_info : # If encoder was created
                encoder_instance.encode_info.dump(
                    status=JOB_STATUS_ERROR_RETRYABLE, # Assume retryable for unexpected
                    last_error_message=f"Pipeline Error: {e}",
                    increment_retry_count=True
                )
                encoder_instance.post_actions() # Attempt cleanup/logging
            # Re-raise for ProcessPoolExecutor to log it as a task failure
            raise


    def post_actions(self):
        """Specific post-actions for the Phone pipeline."""
        self.common_post_actions() # Run common ones first (empty dir removal, combined log)

        if self.process_files_handler:
            # Logic for moving entire "raw" subfolders if they become empty of processable files
            # This was specific to PhoneEncodeStarter in original code.
            # Ensure this makes sense with the new resumable logic.
            # If a folder has files in "error_retryable", it shouldn't be moved.
            if self.args.audio_only and hasattr(self.process_files_handler, "move_raw_folder_if_no_process_files"):
                logger.info(f"[{self.__class__.__name__}] Checking for empty raw audio folders to move.")
                # Pass the root of where "_raw" folders should go (e.g., AUDIO_ENCODED_ROOT_DIR's sibling)
                # The target for raw files is usually <encoded_dir>_raw
                # For audio: AUDIO_ENCODED_RAW_DIR (from config.audio)
                self.process_files_handler.move_raw_folder_if_no_process_files(AUDIO_ENCODED_RAW_DIR)
            elif not self.args.audio_only and hasattr(self.process_files_handler, "move_raw_folder_if_no_process_files"):
                logger.info(f"[{self.__class__.__name__}] Checking for empty raw phone video folders to move.")
                # For phone video, the raw dir is COMPLETED_RAW_DIR / "phone_encoded_raw" (from PhoneVideoEncoder)
                phone_video_raw_target_root = COMPLETED_RAW_DIR / "phone_encoded_raw"
                self.process_files_handler.move_raw_folder_if_no_process_files(phone_video_raw_target_root)

        logger.info(f"[{self.__class__.__name__}] Phone-specific post-actions completed.")


class StandardVideoPipeline(BaseEncodeStarter):
    def __init__(self, project_dir: Path, args: argparse.Namespace):
        super().__init__(project_dir, args)
        self.encoded_dir = VIDEO_OUT_DIR_ROOT.resolve() # Base for all standard video outputs
        # Note: Individual files go into subdirs: self.encoded_dir / media_file.relative_dir
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

    def _initialize_file_processor(self):
        self.process_files_handler = ProcessVideoFiles(self.project_dir, self.args)


    def process_single_file(self, file_path: Path):
        logger.debug(f"StandardPipeline: Processing single file: {file_path}")
        media_file: Optional[MediaFile] = None
        video_encoder: Optional[VideoEncoder] = None

        try:
            media_file = MediaFile(file_path) # Can raise FileNotFoundError, MediaFileException

            # VideoEncoder __init__ will load/initialize EncodeInfo
            video_encoder = VideoEncoder(media_file, self.args)
            job_info = video_encoder.encode_info

            # --- Check EncodeInfo status ---
            if job_info.status == JOB_STATUS_COMPLETED:
                logger.info(f"File {file_path.name} (Standard) is already completed. Finalizing actions.")
                video_encoder.post_actions() # For logging, raw file move
                return
            if job_info.status == JOB_STATUS_SKIPPED:
                logger.info(f"File {file_path.name} (Standard) was previously skipped. Finalizing actions.")
                video_encoder.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_PERMANENT:
                logger.warning(f"File {file_path.name} (Standard) has a permanent error. Skipping.")
                video_encoder.post_actions()
                return
            if job_info.status == JOB_STATUS_ERROR_RETRYABLE and job_info.retry_count >= MAX_ENCODE_RETRIES:
                logger.error(f"File {file_path.name} (Standard) reached max retries ({MAX_ENCODE_RETRIES}). Marking as permanent.")
                job_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Max retries reached.")
                video_encoder.post_actions() # Handles potential error state logging/cleanup
                return

            # --- If not returned, proceed with encoding ---
            logger.debug(f"Starting/Resuming StandardPipeline task for {file_path.name} (Status: {job_info.status})")
            video_encoder.start() # Handles pre-encoding, encoding, and all status updates

        except NoDurationFoundException: # From MediaFile(file_path)
            logger.error(
                f"StandardPipeline: No duration found for {file_path}. Moving to error directory."
            )
            # If MediaFile init fails, VideoEncoder/EncodeInfo might not be available.
            # Original behavior: move file directly.
            # This path means md5 for EncodeInfo might not be available.
            # MediaFile.handle_load_failure should move it.
            # We should ensure this error is distinguishable for not retrying.
            # For now, MediaFile moves it, so this task is "done" from pool's perspective.
            # No EncodeInfo update possible here if md5 unknown.
            # Fallback: Create EncodeInfo using filepath hash if md5 fails? Too complex for now.

            # If media_file object exists (error after some init), try to update EncodeInfo
            if media_file: # Might be None if MediaFile.__init__ itself failed early
                # Try to get an EncodeInfo object to mark permanent error.
                # This is a bit ad-hoc here.
                temp_storage_dir = self.encoded_dir / media_file.relative_dir / ".encode_info_cache"
                temp_storage_dir.mkdir(parents=True, exist_ok=True)
                temp_job_info = EncodeInfo(media_file.md5, storage_dir=temp_storage_dir)
                if temp_job_info.load() or media_file.md5 : # if loaded or md5 available
                    temp_job_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="NoDurationFound during MediaFile init.")

            # Original move logic (could be redundant if MediaFile already moved it)
            error_dest_dir_base = NO_DURATION_FOUND_ERROR_DIR # Absolute path from config
            if media_file: # If we have relative_dir info
                target_error_subdir = error_dest_dir_base / media_file.relative_dir
            else: # Fallback if media_file object is not fully formed
                target_error_subdir = error_dest_dir_base / file_path.parent.name

            target_error_subdir.mkdir(parents=True, exist_ok=True)
            final_error_path = target_error_subdir / file_path.name
            if file_path.exists() and file_path.resolve() != final_error_path.resolve():
                try:
                    shutil.move(str(file_path), str(final_error_path))
                    logger.info(f"Moved {file_path.name} to {final_error_path} due to no duration.")
                except Exception as move_err:
                    logger.error(f"Could not move {file_path.name} to error dir {final_error_path}: {move_err}")
            # This exception type should not be re-raised to stop the pool for other files IF handled by moving.
            # However, if it's a MediaFile init error, it's better to let the pool see it as a task failure.
            # For now, let it propagate to be logged by the pool executor.
            raise

        except Exception as e: # Catch-all for other errors in this scope
            logger.error(f"StandardPipeline: Unhandled error processing file {file_path.name}: {e}", exc_info=True)
            if video_encoder and video_encoder.encode_info: # If encoder was created
                video_encoder.encode_info.dump(
                    status=JOB_STATUS_ERROR_RETRYABLE,
                    last_error_message=f"Pipeline Error: {e}",
                    increment_retry_count=True
                )
                video_encoder.post_actions() # Attempt cleanup/logging
            raise # Re-raise for ProcessPoolExecutor to log


    def _perform_file_management_actions(self, is_final_run: bool = False):
        """Gathers file management actions. `is_final_run` for actions only at very end."""
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.warning(
                "StandardPipeline: File processor not initialized, skipping file management."
            )
            return
        try:
            logger.debug("StandardPipeline: Performing file management actions.")
            self.process_files_handler.remove_empty_dirs()
            self.process_files_handler.delete_temp_folders()

            # Move raw folders only if it's the final run and no more processable files exist in them.
            # This requires checking EncodeInfo for all files in those folders.
            # The current move_raw_folder_if_no_process_files checks only for *new* processable files.
            # This might need adjustment for resumable states.
            if is_final_run and hasattr(self.process_files_handler, "move_raw_folder_if_no_process_files"):
                 logger.info(f"[{self.__class__.__name__}] Checking for empty raw video folders to move (final run).")
                 # Target for standard video raw files: COMPLETED_RAW_DIR (from config.video)
                 self.process_files_handler.move_raw_folder_if_no_process_files(COMPLETED_RAW_DIR)

            SuccessLog.generate_combined_log_yaml(self.project_dir) # Log generation
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
        """Main execution flow for standard video processing."""
        logger.debug(
            f"StandardPipeline: Starting video encoding in path: {self.project_dir}"
        )

        self._initialize_file_processor() # Sets up self.process_files_handler
        if not self.process_files_handler or not self.process_files_handler.source_dir:
            logger.info(
                "StandardPipeline: No source directory found after init, exiting."
            )
            return

        self._perform_file_management_actions(is_final_run=False) # Initial cleanup

        if not self.process_files_handler.source_dir: # Check again if source_dir was removed
            logger.info(
                "StandardPipeline: No source directory after pre-actions, exiting."
            )
            return

        try:
            self.process_multi_file()  # Main concurrent processing loop
        except KeyboardInterrupt: # User interruption
            logger.warning("StandardPipeline: Encoding process interrupted by user. Will attempt final cleanup.")
            # EncodeInfo statuses should reflect interruption if child processes handled signals or were killed.
        except Exception as e:  # Catch unexpected errors from process_multi_file itself
            if sys.version_info >= (3, 10):
                tb_str = traceback.format_exception(e)
            else:
                tb_str = traceback.format_exception(type(e), e, e.__traceback__)
            logger.error(
                f"StandardPipeline: An unexpected error occurred during multi-file processing: {e}\nTraceback: {''.join(tb_str)}"
            )
        finally:
            logger.info("StandardPipeline: Starting final file management actions...")
            self._perform_file_management_actions(is_final_run=True) # Final cleanup / log generation
            logger.info("StandardPipeline: Processing run finished.")