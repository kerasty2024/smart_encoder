"""
This module provides pre-encoding services that analyze media files to determine
the optimal encoding strategy or to identify files that should be skipped.

The pre-encoding step is crucial for an automated workflow, as it makes intelligent
decisions based on the source file's properties, saving time and ensuring
consistent quality in the output. It handles tasks like stream selection (video, audio,
subtitles) and finding the best quality-to-size ratio for encoding.
"""

import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from pprint import pformat
from typing import Optional, List, Dict, Tuple, Type, Any
import shlex
import os
import subprocess

from loguru import logger

# Utility functions for running commands and detecting language.
from ..utils.ffmpeg_utils import (
    run_cmd,
    detect_audio_language_multi_segments,
)
from ..utils.format_utils import format_timedelta

# Domain models and custom exceptions for structured data and error handling.
from ..domain.media import MediaFile
from ..domain.exceptions import (
    PreprocessingException,
    CRFSearchFailedException,
    SkippedFileException,
    NoAudioStreamException,
    FileAlreadyEncodedException,
    BitRateTooLowException,
    FormatExcludedException,
    NoStreamsFoundException,
)
from ..domain.temp_models import EncodeInfo

# Configuration constants for various thresholds, names, and settings.
from ..config.common import (
    LANGUAGE_WORDS,
    MAX_ENCODE_RETRIES,
    JOB_STATUS_PENDING,
    JOB_STATUS_PREPROCESSING_STARTED,
    JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE,
    JOB_STATUS_ERROR_RETRYABLE,
    JOB_STATUS_ERROR_PERMANENT,
    JOB_STATUS_SKIPPED,
    BASE_ERROR_DIR,
)
from ..config.video import (
    VIDEO_OUT_DIR_ROOT,
    VIDEO_COMMENT_ENCODED,
    VIDEO_BITRATE_LOW_THRESHOLD,
    EXCEPT_FORMAT as EXCEPT_VIDEO_FORMATS,
    SAMPLE_EVERY,
    MAX_ENCODED_PERCENT,
    TARGET_VMAF,
    AV1_ENCODER,
    MANUAL_CRF,
    SKIP_VIDEO_CODEC_NAMES,
    ENCODERS as AVAILABLE_ENCODERS,
    VIDEO_NO_AUDIO_FOUND_ERROR_DIR,
)


class PreEncoder:
    """
    An abstract base class for pre-encoding and preprocessing tasks.

    This class provides the foundational logic for analyzing a media file before the
    main encoding process begins. Its responsibilities include:
    - Checking if a file should be skipped (e.g., already encoded, low quality).
    - Determining the optimal encoding parameters (to be implemented by subclasses).
    - Selecting the appropriate streams (video, audio, subtitle) for encoding.
    - Managing the state of the preprocessing stage via an `EncodeInfo` object,
      which allows the process to be resumed if interrupted.
    """

    # Attributes to be populated during the preprocessing workflow.
    media_file: MediaFile
    start_time: datetime
    output_base_dir: Path
    error_dir_base: Path = BASE_ERROR_DIR.resolve()
    renamed_file_on_skip_or_error: Optional[Path] = None
    is_manual_mode: bool
    args: Any
    encode_info_handler: EncodeInfo

    # Results of the preprocessing, to be used by the main encoder.
    best_encoder: str = ""
    best_crf: int = 0
    output_video_streams: List[Dict] = []
    output_audio_streams: List[Dict] = []
    output_subtitle_streams: List[Dict] = []
    crf_checking_time: Optional[timedelta] = None
    best_ratio: Optional[float] = None

    def __init__(
        self,
        media_file: MediaFile,
        manual_mode_flag: bool = False,
        args: Optional[Any] = None,
        comment_tag_encoded: str = "",
        relevant_bitrate_for_check: int = 0,
        relevant_stream_count_for_check: int = 0,
        low_bitrate_threshold_config: int = VIDEO_BITRATE_LOW_THRESHOLD,
        output_base_dir_config: Path = VIDEO_OUT_DIR_ROOT,
    ):
        """
        Initializes the PreEncoder instance.

        Args:
            media_file: The `MediaFile` object to be pre-processed.
            manual_mode_flag: A flag indicating if the process should run in manual mode,
                              bypassing automated checks like CRF search.
            args: The command-line arguments passed to the application.
            comment_tag_encoded: The metadata comment to check for when determining
                                 if a file has already been encoded.
            relevant_bitrate_for_check: The bitrate of the source file, used for
                                        low-quality checks.
            relevant_stream_count_for_check: The number of relevant streams in the source file.
            low_bitrate_threshold_config: The configured bitrate threshold below which
                                          files will be skipped.
            output_base_dir_config: The base directory for all output files.
        """
        self.media_file = media_file
        self.is_manual_mode = manual_mode_flag
        self.args = args
        self.start_time = datetime.now()

        # Configuration for various checks.
        self.comment_tag_for_encoded_check = comment_tag_encoded
        self.bit_rate_relevant = relevant_bitrate_for_check
        self.encode_stream_count_relevant = relevant_stream_count_for_check
        self.bit_rate_low_threshold = low_bitrate_threshold_config

        # Path setup for outputs and logs.
        self.output_base_dir = output_base_dir_config.resolve()
        self.file_specific_output_dir = (
            self.output_base_dir / self.media_file.relative_dir
        )
        self.file_specific_output_dir.mkdir(parents=True, exist_ok=True)
        self.skip_log_path = self.file_specific_output_dir / "pre_encode_skipped.txt"

        # Initialize or load the job's progress from a state file (`.progress.yaml`).
        encode_info_storage_dir = self.file_specific_output_dir / ".encode_info_cache"
        self.encode_info_handler = EncodeInfo(
            self.media_file.md5, storage_dir=encode_info_storage_dir
        )
        if not self.encode_info_handler.load():
            # If no progress file exists, create a new one with 'pending' status.
            self.encode_info_handler.dump(
                status=JOB_STATUS_PENDING,
                ori_video_path=str(self.media_file.path),
                pre_encoder_data={"is_manual_mode": self.is_manual_mode},
            )
        else:
            # If a progress file exists, load the saved data to resume the process.
            if self.encode_info_handler.pre_encoder_data:
                # Restore manual mode flag from the saved state.
                if (
                    self.encode_info_handler.pre_encoder_data.get("is_manual_mode")
                    is not None
                ):
                    self.is_manual_mode = self.encode_info_handler.pre_encoder_data.get(
                        "is_manual_mode"
                    )

                # If resuming from a partially completed state, restore the results.
                if self.encode_info_handler.status in [
                    JOB_STATUS_CRF_SEARCH_STARTED,
                    JOB_STATUS_PREPROCESSING_DONE,
                ]:
                    self.output_video_streams = (
                        self.encode_info_handler.pre_encoder_data.get(
                            "output_video_streams", []
                        )
                    )
                    self.output_audio_streams = (
                        self.encode_info_handler.pre_encoder_data.get(
                            "output_audio_streams", []
                        )
                    )
                    self.output_subtitle_streams = (
                        self.encode_info_handler.pre_encoder_data.get(
                            "output_subtitle_streams", []
                        )
                    )
                    self.best_encoder = self.encode_info_handler.encoder or ""
                    self.best_crf = self.encode_info_handler.crf or 0
                    self.best_ratio = self.encode_info_handler.pre_encoder_data.get(
                        "best_ratio"
                    )
                    time_sec = self.encode_info_handler.pre_encoder_data.get(
                        "crf_checking_time_seconds"
                    )
                    if time_sec is not None:
                        self.crf_checking_time = timedelta(seconds=time_sec)

    def start(self):
        """
        Executes the entire pre-encoding workflow for the media file.

        This method acts as a state machine, orchestrating the various checks and
        parameter-finding processes. It updates the job's status in `EncodeInfo`
        at each major step to ensure fault tolerance.

        Raises:
            SkippedFileException: If the file is intentionally skipped based on pre-defined rules.
            PreprocessingException: For any other recoverable or permanent error during this stage.
        """
        logger.debug(
            f"PreEncoder start for: {self.media_file.filename} (Status: {self.encode_info_handler.status}, Manual: {self.is_manual_mode})"
        )

        # If preprocessing was already completed in a previous run, just load the results and finish.
        if self.encode_info_handler.status == JOB_STATUS_PREPROCESSING_DONE:
            logger.info(
                f"Preprocessing already completed for {self.media_file.filename}. Loading results and skipping pre-encode."
            )
            if self.encode_info_handler.pre_encoder_data:
                self.best_encoder = (
                    self.encode_info_handler.encoder
                    or self.encode_info_handler.pre_encoder_data.get("best_encoder", "")
                )
                self.best_crf = (
                    self.encode_info_handler.crf
                    if self.encode_info_handler.crf is not None
                    else self.encode_info_handler.pre_encoder_data.get("best_crf", 0)
                )
                self.output_video_streams = (
                    self.encode_info_handler.pre_encoder_data.get(
                        "output_video_streams", []
                    )
                )
                self.output_audio_streams = (
                    self.encode_info_handler.pre_encoder_data.get(
                        "output_audio_streams", []
                    )
                )
                self.output_subtitle_streams = (
                    self.encode_info_handler.pre_encoder_data.get(
                        "output_subtitle_streams", []
                    )
                )
                self.is_manual_mode = self.encode_info_handler.pre_encoder_data.get(
                    "is_manual_mode", self.is_manual_mode
                )
                self.best_ratio = self.encode_info_handler.pre_encoder_data.get(
                    "best_ratio"
                )
                time_sec = self.encode_info_handler.pre_encoder_data.get(
                    "crf_checking_time_seconds"
                )
                if time_sec is not None:
                    self.crf_checking_time = timedelta(seconds=time_sec)
                else:
                    self.crf_checking_time = (
                        timedelta(0) if self.is_manual_mode else None
                    )
            else:
                logger.warning(
                    f"Status for {self.media_file.filename} is PREPROCESSING_DONE, but pre_encoder_data is missing. Proceeding cautiously."
                )
            return

        try:
            # 1. Update status to indicate that preprocessing has started.
            if self.encode_info_handler.status not in [JOB_STATUS_CRF_SEARCH_STARTED]:
                current_pre_data = self.encode_info_handler.pre_encoder_data or {}
                current_pre_data["is_manual_mode"] = self.is_manual_mode
                self.encode_info_handler.dump(
                    status=JOB_STATUS_PREPROCESSING_STARTED,
                    pre_encoder_data=current_pre_data,
                )

            # 2. Perform the actual preprocessing steps: skip checks and option determination.
            self._check_if_file_should_be_skipped()
            self._determine_optimal_encoding_options()

            # 3. Save the results of the preprocessing to the state file and mark as done.
            pre_encoder_results = {
                "best_encoder": self.best_encoder,
                "best_crf": self.best_crf,
                "output_video_streams": self.output_video_streams,
                "output_audio_streams": self.output_audio_streams,
                "output_subtitle_streams": self.output_subtitle_streams,
                "crf_checking_time_seconds": self.crf_checking_time.total_seconds()
                if self.crf_checking_time is not None
                else None,
                "best_ratio": self.best_ratio,
                "is_manual_mode": self.is_manual_mode,
            }
            self.encode_info_handler.dump(
                status=JOB_STATUS_PREPROCESSING_DONE,
                encoder=self.best_encoder,
                crf=self.best_crf,
                pre_encoder_data=pre_encoder_results,
            )

        except SkippedFileException as e:
            # If the file was intentionally skipped, update the status and re-raise.
            self.encode_info_handler.dump(
                status=JOB_STATUS_SKIPPED, last_error_message=str(e)
            )
            raise
        except PreprocessingException as e:
            # For controlled, expected errors, update status to retryable or permanent.
            logger.error(
                f"Controlled Preprocessing error for {self.media_file.filename}: {e}"
            )
            is_permanent = isinstance(e, NoAudioStreamException) and not getattr(
                self.args, "allow_no_audio", False
            )
            current_status_on_error = (
                JOB_STATUS_ERROR_PERMANENT
                if is_permanent
                else JOB_STATUS_ERROR_RETRYABLE
            )
            increment_retry_on_error = not is_permanent
            self.encode_info_handler.dump(
                status=current_status_on_error,
                last_error_message=str(e),
                increment_retry_count=increment_retry_on_error,
            )

            if not self.renamed_file_on_skip_or_error:
                if current_status_on_error == JOB_STATUS_ERROR_PERMANENT or (
                    increment_retry_on_error
                    and self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES
                ):
                    error_type_name = type(e).__name__
                    if (
                        increment_retry_on_error
                        and self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES
                    ):
                        error_type_name = f"max_retries_{error_type_name}"
                        self.encode_info_handler.dump(
                            status=JOB_STATUS_ERROR_PERMANENT,
                            last_error_message=f"Max retries: {str(e)}",
                        )
                    self.move_file_to_error_dir(
                        error_subdir_name=f"preproc_err_{error_type_name}"
                    )
            raise
        except Exception as e:
            # For unexpected errors, mark as retryable and log the full traceback.
            logger.error(
                f"Unexpected error during PreEncoder.start for {self.media_file.filename}: {e}",
                exc_info=True,
            )
            self.encode_info_handler.dump(
                status=JOB_STATUS_ERROR_RETRYABLE,
                last_error_message=f"Unexpected PreEncoder: {str(e)}",
                increment_retry_count=True,
            )
            if not self.renamed_file_on_skip_or_error:
                if self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES:
                    self.encode_info_handler.dump(
                        status=JOB_STATUS_ERROR_PERMANENT,
                        last_error_message=f"Max retries, Unexpected PreEncoder: {str(e)}",
                    )
                    self.move_file_to_error_dir(
                        error_subdir_name="preproc_unexpected_err_max_retries"
                    )
            raise PreprocessingException(f"Unexpected pre-encoder failure: {e}") from e

    def _check_if_file_should_be_skipped(self):
        """
        Checks for various conditions to determine if the file should be skipped.

        This method evaluates a set of rules:
        - Is the file already marked as encoded in its metadata?
        - Is the file's bitrate below a quality threshold?
        - Is the file's codec format (e.g., AV1) in the exclusion list?
        - Does the file contain any relevant streams to process?

        If a skip condition is met, it logs the reason, moves the file to a
        'skipped' directory for review, and raises a `SkippedFileException` to
        halt further processing for this file.
        """
        skip_reason: Optional[str] = None
        exception_type: Optional[Type[PreprocessingException]] = None
        log_level_for_skip = logger.info

        # Rule 1: Check if the file has a metadata comment indicating it was already encoded.
        if (
            self.comment_tag_for_encoded_check
            and self.comment_tag_for_encoded_check in self.media_file.comment
        ):
            skip_reason = f"Already encoded (comment tag '{self.comment_tag_for_encoded_check}' found)"
            exception_type = FileAlreadyEncodedException
        # Rule 2: Check if the file's bitrate is below the configured quality threshold.
        elif self.bit_rate_relevant <= self.bit_rate_low_threshold:
            skip_reason = f"Bitrate ({self.bit_rate_relevant}bps) is at or below threshold ({self.bit_rate_low_threshold}bps)"
            exception_type = BitRateTooLowException
        # Rule 3: Check if the file's video format is in the exclusion list (e.g., don't re-encode AV1).
        elif (
            hasattr(self.media_file, "vcodec")
            and self.media_file.vcodec in EXCEPT_VIDEO_FORMATS
        ):
            skip_reason = f"Format '{self.media_file.vcodec}' is in excluded list"
            exception_type = FormatExcludedException
            log_level_for_skip = logger.debug
        # Rule 4: Check if any processable streams were found in the file.
        elif self.encode_stream_count_relevant == 0:
            skip_reason = "No relevant streams to process"
            exception_type = NoStreamsFoundException
            log_level_for_skip = logger.debug

        if skip_reason and exception_type:
            log_level_for_skip(f"Skipping {self.media_file.filename}: {skip_reason}")
            # Write a record of the skip to a log file.
            with self.skip_log_path.open("a", encoding="utf-8") as log_f:
                log_f.write(
                    f"{datetime.now()}: {self.media_file.path} - {skip_reason}\n"
                )

            # Move the skipped file to a dedicated directory for manual review.
            skipped_output_dir = (
                self.file_specific_output_dir / "skipped_by_pre_encoder"
            )
            skipped_output_dir.mkdir(parents=True, exist_ok=True)
            target_skip_path = skipped_output_dir / self.media_file.filename
            if self.media_file.path.exists():
                try:
                    if self.media_file.path.resolve() != target_skip_path.resolve():
                        shutil.move(str(self.media_file.path), str(target_skip_path))
                        self.renamed_file_on_skip_or_error = target_skip_path
                        logger.debug(f"Moved skipped file to {target_skip_path}")
                except Exception as move_err:
                    logger.error(
                        f"Could not move skipped file {self.media_file.filename} to {target_skip_path}: {move_err}"
                    )

            # Raise an exception to halt processing for this file gracefully.
            raise exception_type(skip_reason)

    def _determine_optimal_encoding_options(self):
        """
        An abstract method to determine the best encoding settings.

        Subclasses must implement this to define their specific logic, such as
        selecting streams, finding the best CRF value via analysis, or applying
        fixed settings from a profile.
        """
        raise NotImplementedError(
            "Subclasses must implement _determine_optimal_encoding_options()."
        )

    def move_file_to_error_dir(self, error_subdir_name: str):
        """
        Moves the source file to a structured error directory for later inspection.

        This helps to isolate problematic files and prevent the application from
        repeatedly trying to process them.

        Args:
            error_subdir_name: The name for the subdirectory within the base error
                               directory. This helps to categorize different types of errors.
        """
        if (
            self.renamed_file_on_skip_or_error
            and self.renamed_file_on_skip_or_error.exists()
        ):
            # If the file was already moved (e.g., during a skip check), do nothing.
            return

        target_error_dir = (
            self.error_dir_base / error_subdir_name / self.media_file.relative_dir
        )
        target_error_dir.mkdir(parents=True, exist_ok=True)
        target_error_path = target_error_dir / self.media_file.filename

        if (
            self.media_file.path.exists()
            and self.media_file.path.resolve() != target_error_path.resolve()
        ):
            try:
                shutil.move(str(self.media_file.path), str(target_error_path))
                self.renamed_file_on_skip_or_error = target_error_path
                logger.info(
                    f"Moved problematic file to error directory: {target_error_path}"
                )
            except Exception as e:
                logger.error(
                    f"Failed to move file to error directory {target_error_path}: {e}"
                )


class PreVideoEncoder(PreEncoder):
    """
    A concrete pre-encoder implementation specifically for video files.

    This class orchestrates the video-specific preprocessing logic, which includes:
    1.  Selecting the best video, audio, and subtitle streams to include in the output.
    2.  If not in manual mode, using the `ab-av1` external tool to perform a CRF
        (Constant Rate Factor) search. This search finds the optimal quality/size
        setting that meets a target perceptual quality score (VMAF).
    """

    def __init__(
        self,
        media_file: MediaFile,
        manual_mode_flag: bool = False,
        args: Optional[Any] = None,
    ):
        """Initializes the PreVideoEncoder with video-specific configurations."""
        super().__init__(
            media_file=media_file,
            manual_mode_flag=manual_mode_flag,
            args=args,
            comment_tag_encoded=VIDEO_COMMENT_ENCODED,
            relevant_bitrate_for_check=media_file.vbitrate,
            relevant_stream_count_for_check=media_file.video_stream_count,
            low_bitrate_threshold_config=VIDEO_BITRATE_LOW_THRESHOLD,
            output_base_dir_config=VIDEO_OUT_DIR_ROOT,
        )
        self.available_encoders_cfg: Tuple[str, ...] = AVAILABLE_ENCODERS

    def _determine_optimal_encoding_options(self):
        """
        Determines the optimal encoding options by selecting streams and finding the best CRF.
        This is the core logic of the PreVideoEncoder.
        """
        # --- Step 1: Select Streams ---
        # If streams haven't been selected yet (i.e., this is not a resumed job).
        if not (
            self.output_video_streams
            or self.output_audio_streams
            or self.output_subtitle_streams
        ):
            logger.debug(f"Performing stream selection for {self.media_file.filename}.")
            self._select_output_video_streams()
            try:
                self._select_output_audio_streams()
            except NoAudioStreamException as e:
                # If no suitable audio is found, only proceed if the user has explicitly allowed it.
                if getattr(self.args, "allow_no_audio", False):
                    logger.warning(
                        f"No suitable audio stream found, but proceeding without audio due to --allow-no-audio flag."
                    )
                    self.output_audio_streams = []
                else:
                    raise  # Re-raise the exception to mark the job as failed.
            self._select_output_subtitle_streams()
            # Persist the stream selections in the state file for resumability.
            current_pre_data = self.encode_info_handler.pre_encoder_data or {}
            current_pre_data.update(
                {
                    "output_video_streams": self.output_video_streams,
                    "output_audio_streams": self.output_audio_streams,
                    "output_subtitle_streams": self.output_subtitle_streams,
                    "is_manual_mode": self.is_manual_mode,
                }
            )
            self.encode_info_handler.dump(pre_encoder_data=current_pre_data)
        else:
            logger.debug(
                f"Stream selections already populated for {self.media_file.filename} (likely from a resumed job)."
            )

        # --- Step 2: Determine CRF (Quality Setting) ---
        if self.is_manual_mode:
            # In manual mode, use a fixed CRF value from the configuration.
            self.best_crf = MANUAL_CRF
            self.best_encoder = (
                self.available_encoders_cfg[0]
                if self.available_encoders_cfg
                else AV1_ENCODER
            )
            self.crf_checking_time = timedelta(0)
            self.best_ratio = None
            logger.info(
                f"Manual mode: Using fixed Encoder {self.best_encoder}, CRF {self.best_crf}."
            )
            return

        # If not in manual mode, perform the automated CRF search.
        if self.encode_info_handler.status not in [
            JOB_STATUS_CRF_SEARCH_STARTED,
            JOB_STATUS_PREPROCESSING_DONE,
        ]:
            self.encode_info_handler.dump(
                status=JOB_STATUS_CRF_SEARCH_STARTED,
                encoder="",
                crf=0,
                pre_encoder_data=self.encode_info_handler.pre_encoder_data,
            )

        crf_search_start_time = datetime.now()
        current_best_ratio_found: Optional[float] = self.best_ratio
        start_encoder_index = 0
        # Logic to resume CRF search from the last attempted encoder.
        if (
            self.encode_info_handler.status == JOB_STATUS_CRF_SEARCH_STARTED
            and self.encode_info_handler.encoder
        ):
            try:
                encoder_in_progress = self.encode_info_handler.encoder
                start_encoder_index = self.available_encoders_cfg.index(
                    encoder_in_progress
                )
                if self.encode_info_handler.crf != 0:
                    start_encoder_index += 1
            except ValueError:
                logger.warning(
                    f"Resuming with an unknown encoder. Restarting CRF search."
                )
                start_encoder_index = 0
                current_best_ratio_found = None
                self.best_encoder, self.best_crf, self.best_ratio = "", 0, None

        all_searches_failed_flag = True
        for i in range(start_encoder_index, len(self.available_encoders_cfg)):
            encoder_candidate = self.available_encoders_cfg[i]
            logger.debug(f"Starting CRF search for encoder: {encoder_candidate}")
            try:
                self.encode_info_handler.dump(encoder=encoder_candidate, crf=0)
                crf_val, encoded_ratio_val = self._perform_crf_search_for_encoder(
                    encoder_candidate
                )
                all_searches_failed_flag = False

                # If this encoder gives a better (smaller) file size ratio, update our best result.
                if (
                    current_best_ratio_found is None
                    or (encoded_ratio_val / 100.0) < current_best_ratio_found
                ):
                    current_best_ratio_found = encoded_ratio_val / 100.0
                    self.best_encoder, self.best_crf, self.best_ratio = (
                        encoder_candidate,
                        crf_val,
                        current_best_ratio_found,
                    )
                    self.encode_info_handler.dump(
                        encoder=self.best_encoder, crf=self.best_crf
                    )
            except (CRFSearchFailedException, Exception) as e:
                logger.warning(
                    f"CRF search failed for encoder {encoder_candidate}: {e}. Trying next encoder."
                )
                continue

        self.crf_checking_time = datetime.now() - crf_search_start_time

        # If all encoder searches failed, fall back to manual settings.
        if all_searches_failed_flag or not self.best_encoder or self.best_crf == 0:
            logger.warning(
                "CRF search failed for all configured encoders. Falling back to manual mode settings."
            )
            self.is_manual_mode = True
            self.best_crf, self.best_encoder = (
                MANUAL_CRF,
                (
                    self.available_encoders_cfg[0]
                    if self.available_encoders_cfg
                    else AV1_ENCODER
                ),
            )
            self.best_ratio, self.crf_checking_time = None, timedelta(0)
        else:
            ratio_log_str = (
                f"{self.best_ratio:.2%}" if self.best_ratio is not None else "N/A"
            )
            logger.info(
                f"Optimal pre-encode params found: Encoder {self.best_encoder}, CRF {self.best_crf}, Ratio {ratio_log_str}. Time: {format_timedelta(self.crf_checking_time)}"
            )

    def _perform_crf_search_for_encoder(self, encoder_to_test: str) -> Tuple[int, int]:
        """
        Executes the `ab-av1 crf-search` command to find the best CRF for a given encoder.

        `ab-av1` is an external tool that automates finding a CRF value to meet a
        target visual quality (VMAF) without exceeding a certain file size ratio.

        Args:
            encoder_to_test: The name of the encoder to test (e.g., 'libsvtav1').

        Returns:
            A tuple containing the best CRF value and the resulting file size ratio (as a percentage).

        Raises:
            CRFSearchFailedException: If the `ab-av1` tool fails or returns invalid results.
        """
        if (
            self.renamed_file_on_skip_or_error
            and self.renamed_file_on_skip_or_error.exists()
        ):
            raise SkippedFileException(
                f"File no longer at original path for CRF search."
            )

        # Build the command for the ab-av1 tool.
        temp_dir = (
            str(self.args.temp_work_dir.resolve())
            if getattr(self.args, "temp_work_dir", None)
            else None
        )
        cmd_list = [
            "ab-av1",
            "crf-search",
            "-e",
            encoder_to_test,
            "-i",
            str(self.media_file.path.resolve()),
            "--sample-every",
            SAMPLE_EVERY,
            "--max-encoded-percent",
            str(MAX_ENCODED_PERCENT),
            "--min-vmaf",
            str(TARGET_VMAF),
        ]
        if temp_dir:
            cmd_list.extend(["--temp-dir", temp_dir])

        display_cmd = (
            subprocess.list2cmdline(cmd_list)
            if os.name == "nt"
            else " ".join(shlex.quote(s) for s in cmd_list)
        )
        logger.debug(
            f"Executing CRF search for {self.media_file.filename} with {encoder_to_test}: {display_cmd}"
        )
        res = run_cmd(
            cmd_list, src_file_for_log=self.media_file.path, show_cmd=__debug__
        )

        if res is None or res.returncode != 0:
            err_msg = f"ab-av1 crf-search failed for {encoder_to_test}. RC: {res.returncode if res else 'N/A'}."
            # ... [Logging of ab-av1 error output to a debug file] ...
            raise CRFSearchFailedException(err_msg)

        # Parse the command's standard output to find the "CRF" and "ratio" values.
        stdout_lower = res.stdout.lower()
        crf_match = re.search(r"crf\s+(\d+)", stdout_lower)
        ratio_match = re.search(r"\((\d+)%\)", stdout_lower) or re.search(
            r"ratio\s+(\d+)%", stdout_lower
        )

        if crf_match and ratio_match:
            crf = int(crf_match.group(1))
            ratio = int(ratio_match.group(1))
            if crf <= 0 or ratio <= 0 or ratio > MAX_ENCODED_PERCENT + 15:
                raise CRFSearchFailedException(
                    f"Invalid results from ab-av1: CRF {crf}, Ratio {ratio}%"
                )
            return crf, ratio
        else:
            # ... [Logging of ab-av1 output to a debug file if parsing fails] ...
            raise CRFSearchFailedException(
                "Could not parse CRF and/or Ratio from ab-av1 output."
            )

    def _select_output_video_streams(self):
        """
        Selects the video stream(s) to be included in the final encode.

        This method filters the available video streams, excluding any with unusual
        frame rates or codecs that are on the skip list. Currently, it selects the
        first valid stream found.
        """
        if not self.media_file.video_streams:
            self.output_video_streams = []
            return

        valid_streams = []
        for stream in self.media_file.video_streams:
            is_valid_fps, is_valid_codec = False, False
            try:
                num, den = map(int, stream.get("avg_frame_rate", "0/0").split("/"))
                if (
                    den != 0 and 1 <= (num / den) < 121
                ):  # Check for a reasonable FPS range.
                    is_valid_fps = True
            except (ValueError, ZeroDivisionError):
                pass

            if stream.get("codec_name", "").lower() not in SKIP_VIDEO_CODEC_NAMES:
                is_valid_codec = True

            if is_valid_fps and is_valid_codec:
                valid_streams.append(stream)

        # If valid streams are found, pick the first one. Otherwise, fall back to the very first stream as a last resort.
        if valid_streams:
            self.output_video_streams = [valid_streams[0]]
        elif self.media_file.video_streams:
            logger.warning(
                f"No video streams with typical FPS found. Using the first stream found as a fallback."
            )
            self.output_video_streams = [self.media_file.video_streams[0]]
        else:
            self.output_video_streams = []

        if not self.output_video_streams:
            logger.warning(
                f"No suitable video streams selected for {self.media_file.filename}."
            )

    def _select_output_audio_streams(self):
        """
        Selects suitable audio streams based on their language.

        This complex but important method prioritizes streams with explicit language tags
        that match the configured `LANGUAGE_WORDS`. For streams with an 'undetermined'
        tag, it uses a machine learning model (Whisper) to detect the language of
        audio segments, providing a robust way to select the desired audio track.
        """
        if not self.media_file.audio_streams:
            if not getattr(self.args, "allow_no_audio", False):
                raise NoAudioStreamException(
                    f"No audio streams found in {self.media_file.filename}."
                )
            else:
                self.output_audio_streams = []
                return

        # If there's only one audio stream, select it by default to save time.
        if len(self.media_file.audio_streams) == 1:
            stream = self.media_file.audio_streams[0]
            # Perform a basic sanity check on the sample rate.
            try:
                if int(float(stream.get("sample_rate", 0))) >= 8000:
                    self.output_audio_streams = [stream]
                    return
            except (ValueError, TypeError):
                pass
            # If the single stream is unsuitable, treat as no audio found.
            if not getattr(self.args, "allow_no_audio", False):
                raise NoAudioStreamException(
                    "Single audio stream is unsuitable (e.g., invalid sample rate)."
                )
            else:
                self.output_audio_streams = []
                return

        # For multiple streams, filter them by language.
        suitable_streams = [
            s
            for s in self.media_file.audio_streams
            if self._is_audio_stream_language_suitable(s)
        ]

        if not suitable_streams:
            if not getattr(self.args, "allow_no_audio", False):
                raise NoAudioStreamException(
                    f"No audio streams match desired languages for {self.media_file.filename}."
                )
            else:
                self.output_audio_streams = []
                return

        self.output_audio_streams = suitable_streams
        logger.debug(
            f"Selected {len(self.output_audio_streams)} audio streams for {self.media_file.filename}."
        )

    def _is_audio_stream_language_suitable(self, stream_data: Dict) -> bool:
        """
        A helper method that checks if a single audio stream is in a desired language.
        It first checks the stream's metadata tag, and if that is inconclusive, it
        uses language detection on the audio itself.
        """
        lang_tag = stream_data.get("tags", {}).get("language", "und").lower().strip()
        # 1. Check the explicit language tag first.
        if lang_tag in LANGUAGE_WORDS:
            return True
        if lang_tag != "und":  # If tag exists but is not in our list, it's unsuitable.
            return False

        # 2. If tag is 'undetermined', use language detection as a fallback.
        try:
            temp_dir = (
                Path(self.args.temp_work_dir)
                if getattr(self.args, "temp_work_dir", None)
                else None
            )
            detected_lang = detect_audio_language_multi_segments(
                self.media_file.path,
                stream_data,
                total_media_duration_seconds=int(self.media_file.duration),
                temp_work_dir_override=temp_dir,
            ).lower()
            return detected_lang in LANGUAGE_WORDS
        except Exception as e:
            logger.error(
                f"Language detection failed for stream {stream_data.get('index')}: {e}"
            )
            return False

    def _select_output_subtitle_streams(self):
        """
        Selects suitable subtitle streams based on their language tag.
        """
        if not self.media_file.subtitle_streams:
            self.output_subtitle_streams = []
            return
        # Select subtitles that have a language tag matching our list of desired languages.
        self.output_subtitle_streams = [s for s in self.media_file.subtitle_streams if s.get("tags", {}).get("language", "").lower().strip() in LANGUAGE_WORDS]
        logger.debug(f"Selected {len(self.output_subtitle_streams)} subtitle streams for {self.media_file.filename}.")