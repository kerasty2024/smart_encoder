import os
import platform
import shutil
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from pprint import pformat
from typing import Optional, Any # Added Any

import yaml
from loguru import logger

# Utils
from ..utils.format_utils import format_timedelta, formatted_size
from ..utils.ffmpeg_utils import run_cmd

# Domain
from ..domain.media import MediaFile
from ..domain.exceptions import (
    SkippedFileException, # Though PreEncoder primarily raises this
    MP4MKVEncodeFailException,
    EncodingException,
    PreprocessingException, # For PreEncoder interaction
    # FormatExcludedException, NoAudioStreamException, (Handled by PreEncoder)
)
from ..domain.temp_models import EncodeInfo

# Services
from .logging_service import ErrorLog, SuccessLog
from .preprocessing_service import PreVideoEncoder, PreEncoder # PreEncoder for type hint

# Config
from ..config.audio import (
    DEFAULT_AUDIO_ENCODER,
    TARGET_BIT_RATE_IPHONE_XR,
    AUDIO_ENCODED_ROOT_DIR,
    AUDIO_ENCODED_RAW_DIR,
    AUDIO_COMMENT_ENCODED,
)
from ..config.common import (
    COMMAND_TEXT, BASE_ERROR_DIR, MAX_ENCODE_RETRIES,
    JOB_STATUS_PENDING, JOB_STATUS_PREPROCESSING_STARTED, JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE, JOB_STATUS_ENCODING_FFMPEG_STARTED, JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR_RETRYABLE, JOB_STATUS_ERROR_PERMANENT, JOB_STATUS_SKIPPED
)
from ..config.video import (
    VIDEO_OUT_DIR_ROOT,
    AUDIO_OPUS_CODECS,
    SUBTITLE_MKV_CODECS,
    OPUS_ENCODER,
    TARGET_VMAF,
    VIDEO_COMMENT_ENCODED,
    COMPLETED_RAW_DIR,
    VIDEO_CODEC_IPHONE_XR,
    AUDIO_CODEC_IPHONE_XR,
    MANUAL_VIDEO_BIT_RATE_IPHONE_XR,
    OUTPUT_DIR_IPHONE,
    MANUAL_AUDIO_BIT_RATE_IPHONE_XR,
    IPHONE_XR_OPTIONS,
    ENCODERS as VIDEO_ENCODERS_CONFIG,
    MANUAL_CRF_INCREMENT_PERCENT,
    MAX_CRF,
)


class Encoder:
    pre_encoder: Optional[PreEncoder] = None # More generic type hint
    encode_start_datetime: datetime
    encode_end_datetime: datetime
    encode_time: timedelta
    total_time: timedelta # Includes pre-encoding time
    encoder_codec_name: str # Set by subclass or pre-encoder
    crf: Optional[int] = None # Set by subclass or pre-encoder
    encoded_dir: Path # Must be set by subclass __init__
    encoded_root_dir: Path # Usually same as encoded_dir or its parent, for context
    encoded_file: Path # Actual output file, must be set by subclass __init__ or encode()
    encoded_size: int
    renamed_original_file: Optional[Path] = None # If pre-encoder skipped/moved
    success_log: Optional[SuccessLog] = None
    encode_info: EncodeInfo # Progress tracking object

    def __init__(self, media_file: MediaFile, args: Any): # Use Any for args
        self.original_media_file = media_file
        self.args = args
        self.no_error = True # Internal flag for current ffmpeg op, not overall job status

        self.error_dir_base = BASE_ERROR_DIR.resolve()
        self.current_error_output_path: Optional[Path] = None # If original moved to error dir

        self.success_log_dir_base = Path.cwd() # Default, can be overridden by subclass
        self.encoded_comment_text = ""
        self.encode_cmd_str = "" # Current ffmpeg command
        self.encoded_raw_files_target_dir: Optional[Path] = None # Set by subclass

        self.keep_mtime = args.keep_mtime if hasattr(args, "keep_mtime") else False

        # Initialize or get EncodeInfo instance
        # Subclasses (like VideoEncoder) that use PreEncoder will get it from there.
        # Others (AudioEncoder, PhoneVideoEncoder) will initialize it here.
        # This logic is a bit complex here, ideally EncodeInfo is always passed or consistently retrieved.
        # For now, relying on subclasses to ensure self.encode_info is set.
        # If pre_encoder is used (e.g. VideoEncoder), it will set self.encode_info in its __init__
        # otherwise, it needs to be set here.
        # This will be set by subclasses after super().__init__()
        # self.encode_info = ...


    def start(self):
        # Ensure encode_info is initialized by the subclass or a pre_encoder
        if not hasattr(self, 'encode_info') or not self.encode_info:
            logger.warning(f"EncodeInfo not set for {self.original_media_file.filename} before Encoder.start(). Initializing a default one.")
            default_storage_dir = Path.cwd() / ".smart_encoder_cache" / self.original_media_file.md5[:2]
            default_storage_dir.mkdir(parents=True, exist_ok=True)
            self.encode_info = EncodeInfo(self.original_media_file.md5, storage_dir=default_storage_dir)
            if not self.encode_info.load():
                self.encode_info.dump(status=JOB_STATUS_PENDING, ori_video_path=str(self.original_media_file.path))


        logger.info(
            f"Starting processing for: {self.original_media_file.path.relative_to(Path.cwd())} (Status: {self.encode_info.status})"
        )
        if not hasattr(self, "encoded_dir") or not self.encoded_dir: # Should be set by subclass
            logger.error(f"encoded_dir not set for {self.original_media_file.filename} before start() call.")
            self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Internal error: encoded_dir not set.")
            self.no_error = False # Ensure post_actions knows about the error
            self.post_actions()
            return
        self.encoded_dir.mkdir(parents=True, exist_ok=True)


        # --- Check overall job status from EncodeInfo ---
        if self.encode_info.status == JOB_STATUS_COMPLETED:
            logger.info(f"File {self.original_media_file.filename} already completed. Finalizing.")
            self.no_error = True # Overall job is success
            if self.encode_info.temp_output_path:
                self.encoded_file = Path(self.encode_info.temp_output_path)
                if self.encoded_file.exists():
                    self.encoded_size = self.encoded_file.stat().st_size
            if not hasattr(self, 'encode_time'): self.encode_time = timedelta(0)
            if not hasattr(self, 'total_time'): self.total_time = timedelta(0)
            if not hasattr(self, 'encode_end_datetime'): self.encode_end_datetime = datetime.fromisoformat(self.encode_info.last_updated) if self.encode_info.last_updated else datetime.now()

            self.write_success_log()
            self.post_actions()
            return

        if self.encode_info.status == JOB_STATUS_SKIPPED:
            logger.info(f"File {self.original_media_file.filename} was previously skipped. Finalizing.")
            self.no_error = True
            if self.pre_encoder and self.pre_encoder.renamed_file_on_skip_or_error:
                 self.renamed_original_file = self.pre_encoder.renamed_file_on_skip_or_error
            self.post_actions()
            return

        if self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
            logger.error(f"File {self.original_media_file.filename} has a permanent error. Not processing.")
            self.no_error = False
            self.post_actions()
            return

        # --- Pre-encoder execution (if applicable, e.g., VideoEncoder) ---
        if self.pre_encoder:
            try:
                # PreEncoder.start() should be called if pre-processing is not yet done.
                # It will handle its own status updates within EncodeInfo.
                if self.encode_info.status not in [JOB_STATUS_PREPROCESSING_DONE, JOB_STATUS_ENCODING_FFMPEG_STARTED]:
                    logger.debug(f"Pre-encoder required for {self.original_media_file.filename}, current status: {self.encode_info.status}. Running pre_encoder.start().")
                    self.pre_encoder.start() # Can raise SkippedFileException, PreprocessingException
                else:
                    logger.debug(f"Pre-encoder stage already completed or bypassed for {self.original_media_file.filename} (status: {self.encode_info.status}). Loading results for encoding.")
                    # Ensure PreEncoder attributes are populated if its start() was skipped
                    if self.encode_info.status == JOB_STATUS_PREPROCESSING_DONE or self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED:
                        if self.encode_info.pre_encoder_data:
                            self.pre_encoder.best_encoder = self.encode_info.encoder or self.encode_info.pre_encoder_data.get("best_encoder", "")
                            self.pre_encoder.best_crf = self.encode_info.crf if self.encode_info.crf is not None else self.encode_info.pre_encoder_data.get("best_crf", 0)
                            self.pre_encoder.output_video_streams = self.encode_info.pre_encoder_data.get("output_video_streams", [])
                            self.pre_encoder.output_audio_streams = self.encode_info.pre_encoder_data.get("output_audio_streams", [])
                            self.pre_encoder.output_subtitle_streams = self.encode_info.pre_encoder_data.get("output_subtitle_streams", [])
                            self.pre_encoder.is_manual_mode = self.encode_info.pre_encoder_data.get("is_manual_mode", self.pre_encoder.is_manual_mode)
                            self.pre_encoder.best_ratio = self.encode_info.pre_encoder_data.get("best_ratio")
                            time_sec = self.encode_info.pre_encoder_data.get("crf_checking_time_seconds")
                            if time_sec is not None:
                                self.pre_encoder.crf_checking_time = timedelta(seconds=time_sec)
                        else:
                            logger.warning(f"Status is {self.encode_info.status} but no pre_encoder_data found in EncodeInfo for {self.original_media_file.filename}. Encoding may fail.")


                if self.pre_encoder.renamed_file_on_skip_or_error and \
                   self.pre_encoder.renamed_file_on_skip_or_error.exists() and \
                   self.pre_encoder.renamed_file_on_skip_or_error.resolve() != self.original_media_file.path.resolve():
                    self.renamed_original_file = self.pre_encoder.renamed_file_on_skip_or_error
                    self.no_error = (self.encode_info.status == JOB_STATUS_SKIPPED)
                    self.post_actions()
                    return

                if self.encode_info.status == JOB_STATUS_PREPROCESSING_DONE:
                    self.encoder_codec_name = self.encode_info.encoder
                    self.crf = self.encode_info.crf

            except SkippedFileException as e:
                logger.info(f"Skipped by pre-encoder: {self.original_media_file.filename} - {e}")
                if self.pre_encoder.renamed_file_on_skip_or_error:
                    self.renamed_original_file = self.pre_encoder.renamed_file_on_skip_or_error
                self.no_error = True
                self.post_actions()
                return
            except PreprocessingException as pe:
                logger.error(f"Pre-processing error for {self.original_media_file.filename}: {pe}")
                self.no_error = False
                if self.pre_encoder.renamed_file_on_skip_or_error:
                    self.current_error_output_path = self.pre_encoder.renamed_file_on_skip_or_error
                self.post_actions()
                return
            except Exception as pe_general:
                logger.error(
                    f"Unexpected error during pre-encoding for {self.original_media_file.filename}: {pe_general}",
                    exc_info=True,
                )
                self.no_error = False
                if self.pre_encoder and self.pre_encoder.renamed_file_on_skip_or_error:
                    self.current_error_output_path = self.pre_encoder.renamed_file_on_skip_or_error
                self.post_actions()
                return

        if self.no_error and self.encode_info.status not in [JOB_STATUS_COMPLETED, JOB_STATUS_SKIPPED, JOB_STATUS_ERROR_PERMANENT]:
            self._perform_encode()

        self.post_actions()


    def _perform_encode(self):
        if self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED or \
           (self.encode_info.status == JOB_STATUS_ERROR_RETRYABLE and self.encode_info.ffmpeg_command):

            action = "Resuming" if self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED else f"Retrying (Attempt {self.encode_info.retry_count + 1})"
            logger.info(f"{action} encoding for {self.original_media_file.filename}.")

            if self.encode_info.temp_output_path:
                temp_file_to_delete = Path(self.encode_info.temp_output_path)
                if temp_file_to_delete.exists():
                    logger.debug(f"Deleting potentially incomplete previous output file: {temp_file_to_delete}")
                    try:
                        temp_file_to_delete.unlink(missing_ok=True)
                    except OSError as e:
                        logger.error(f"Could not delete {temp_file_to_delete}: {e}")
                        self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=f"Failed to delete temp file: {e}")
                        self.no_error = False
                        return


        self.encode_start_datetime = datetime.now()
        logger.info(
            f"Encoding: {self.original_media_file.path.relative_to(Path.cwd())}"
        )
        self.no_error = True

        try:
            self.encode()
        except EncodingException as enc_ex:
            logger.error(f"EncodingException caught for {self.original_media_file.filename}: {enc_ex}")
            return
        except Exception as general_enc_ex:
            logger.error(
                f"Unexpected exception during encode() method for {self.original_media_file.filename}: {general_enc_ex}",
                exc_info=True,
            )
            self.no_error = False
            self.encode_info.dump(status=JOB_STATUS_ERROR_RETRYABLE,
                                  last_error_message=f"Unexpected encode error: {general_enc_ex}",
                                  increment_retry_count=True)
            return

        if self.no_error:
            self.encode_end_datetime = datetime.now()
            self.encode_time = self.encode_end_datetime - self.encode_start_datetime

            pre_encode_time = timedelta(0)
            if self.pre_encoder and self.pre_encoder.crf_checking_time:
                pre_encode_time = self.pre_encoder.crf_checking_time
            elif self.encode_info.pre_encoder_data and self.encode_info.pre_encoder_data.get("crf_checking_time_seconds") is not None:
                pre_encode_time = timedelta(seconds=self.encode_info.pre_encoder_data["crf_checking_time_seconds"])

            self.total_time = pre_encode_time + self.encode_time

            self.encode_info.dump(status=JOB_STATUS_COMPLETED, temp_output_path=str(self.encoded_file))
            self.write_success_log()
        else:
            logger.warning(
                f"Encoding operation for {self.original_media_file.filename} finished with no_error=False. Status: {self.encode_info.status}"
            )


    def failed_action(self, res_stdout: str, res_stderr: str, return_code: int, is_retryable_error: bool = True):
        self.no_error = False
        error_message_short = f"ffmpeg failed (rc={return_code})"
        error_message_full = (f"ffmpeg command failed for {self.original_media_file.path}, encoder: {getattr(self, 'encoder_codec_name', 'N/A')} "
                              f"return code: ({return_code}):\nSTDOUT: {res_stdout}\nSTDERR: {res_stderr}")
        logger.error(error_message_full)

        error_file_dir = (
            self.error_dir_base
            / f"ffmpeg_rc_{return_code}"
            / self.original_media_file.relative_dir
        )
        error_file_dir.mkdir(parents=True, exist_ok=True)
        error_log = ErrorLog(error_file_dir)
        error_log.write(
            f"Failed command: {self.encode_cmd_str}",
            f"Original file: {str(self.original_media_file.path)}",
            f"MD5: {self.original_media_file.md5}",
            f"Probe info (brief): Video streams: {len(self.original_media_file.video_streams)}, Audio: {len(self.original_media_file.audio_streams)}",
            f"Stdout: {res_stdout}",
            f"Stderr: {res_stderr}",
        )

        if is_retryable_error and self.encode_info.retry_count < MAX_ENCODE_RETRIES:
            self.encode_info.dump(status=JOB_STATUS_ERROR_RETRYABLE,
                                  last_error_message=error_message_short,
                                  increment_retry_count=True)
            logger.info(f"Marked {self.original_media_file.filename} as retryable. Retry {self.encode_info.retry_count}/{MAX_ENCODE_RETRIES}.")
        else:
            final_error_reason = error_message_short
            if not is_retryable_error:
                logger.error(f"Non-retryable ffmpeg error for {self.original_media_file.filename}.")
            else:
                final_error_reason = f"Max retries ({MAX_ENCODE_RETRIES}) reached. Last error: {error_message_short}"
                logger.error(final_error_reason)

            self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT,
                                  last_error_message=final_error_reason)

            perm_error_dir = (
                self.error_dir_base
                / "ffmpeg_permanent_error"
                / self.original_media_file.relative_dir
            )
            perm_error_dir.mkdir(parents=True, exist_ok=True)
            target_error_path = perm_error_dir / self.original_media_file.filename

            if self.original_media_file.path.exists():
                if self.original_media_file.path.resolve() != target_error_path.resolve():
                    try:
                        shutil.move(str(self.original_media_file.path), str(target_error_path))
                        logger.info(
                            f"Moved original file {self.original_media_file.filename} to {target_error_path} (Permanent ffmpeg Error)"
                        )
                        self.current_error_output_path = target_error_path
                    except Exception as e:
                        logger.error(
                            f"Could not move original file {self.original_media_file.filename} to error dir {target_error_path}: {e}"
                        )
            else:
                logger.warning(
                    f"Original file {self.original_media_file.path} not found for moving to permanent error dir."
                )
                self.current_error_output_path = target_error_path

        if hasattr(self, "encoded_file") and self.encoded_file and self.encoded_file.exists():
            try:
                self.encoded_file.unlink(missing_ok=True)
                logger.info(f"Deleted partially encoded file: {self.encoded_file}")
            except OSError as e:
                logger.error(
                    f"Could not delete partially encoded file {self.encoded_file}: {e}"
                )


    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        if self.encode_info.status != JOB_STATUS_COMPLETED:
            logger.debug(
                f"Skipping success log for {self.original_media_file.filename}: job status is {self.encode_info.status}"
            )
            return

        if not hasattr(self, "encoded_file") or not self.encoded_file or not self.encoded_file.exists():
            logger.error(
                f"Cannot write success log for {self.original_media_file.filename}: encoded file missing at {getattr(self, 'encoded_file', 'N/A')}"
            )
            self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Encoded file missing after completion.")
            return

        try:
            if not hasattr(self, 'encoded_size') or self.encoded_size == 0 :
                self.encoded_size = self.encoded_file.stat().st_size
        except FileNotFoundError:
            logger.error(
                f"Success log: Encoded file {self.encoded_file} not found for size check."
            )
            self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Encoded file not found for success log.")
            return

        if not hasattr(self, 'encode_end_datetime'): self.encode_end_datetime = datetime.fromisoformat(self.encode_info.last_updated) if self.encode_info.last_updated else datetime.now()
        if not hasattr(self, 'total_time'): self.total_time = timedelta(0)
        if not hasattr(self, 'encode_time'): self.encode_time = timedelta(0)

        log_dict = {
            "index": 0,
            "input_file": str(self.original_media_file.path),
            "source_file_md5": self.original_media_file.md5,
            "source_file_sha256": self.original_media_file.sha256,
            "encoder_codec": getattr(self, "encoder_codec_name", self.encode_info.encoder or "N/A"),
            "crf": getattr(self, "crf", self.encode_info.crf if self.encode_info.crf is not None else "N/A"),
            "file_duration_seconds": self.original_media_file.duration,
            "file_duration_formatted": format_timedelta(
                timedelta(seconds=self.original_media_file.duration)
            ),
            "total_elapsed_time_formatted": format_timedelta(self.total_time),
            "encode_time_formatted": format_timedelta(self.encode_time),
            "encode_efficiency_factor": round(
                self.encode_time.total_seconds() / self.original_media_file.duration, 2
            )
            if self.original_media_file.duration > 0 and self.encode_time.total_seconds() > 0
            else "N/A",
            "encoded_size_bytes": self.encoded_size,
            "encoded_size_formatted": formatted_size(self.encoded_size),
            "original_size_bytes": self.original_media_file.size,
            "original_size_formatted": formatted_size(self.original_media_file.size),
            "size_ratio_percent": round(
                (self.encoded_size / self.original_media_file.size) * 100, 2
            )
            if self.original_media_file.size > 0
            else "N/A",
            "ended_datetime": self.encode_end_datetime.strftime("%Y%m%d_%H:%M:%S"),
            "encoded_file_path": str(self.encoded_file.resolve()),
            "processor_info": platform.processor(),
            "platform_info": platform.platform(),
        }

        if update_dic:
            log_dict.update(update_dic)

        log_storage_path = getattr(
            self, "success_log_output_dir", self.success_log_dir_base
        )

        self.success_log = SuccessLog(
            log_storage_path, use_dated_filename=log_date_in_filename
        )
        self.success_log.write(log_dict)
        logger.debug(
            f"Success log written for {self.original_media_file.filename} to {self.success_log.log_file_path}"
        )


    def encode(self):
        raise NotImplementedError("Subclasses must implement the encode() method.")


    def _move_original_raw_file(self):
        if self.encode_info.status != JOB_STATUS_COMPLETED:
            logger.debug(f"Skipping move of raw file for {self.original_media_file.filename} as job status is '{self.encode_info.status}'.")
            return

        if not self.encoded_raw_files_target_dir:
            logger.debug(
                f"Raw file target directory not set for {self.original_media_file.filename}, skipping move."
            )
            return

        if not self.original_media_file.path.exists():
            if self.renamed_original_file and self.renamed_original_file.parent.resolve() == self.encoded_raw_files_target_dir.resolve():
                 logger.debug(f"Original file {self.original_media_file.filename} seems already in raw archive: {self.renamed_original_file}")
                 return
            logger.warning(
                f"Original file {self.original_media_file.path} does not exist, cannot move to raw archive."
            )
            return

        self.encoded_raw_files_target_dir.mkdir(parents=True, exist_ok=True)
        raw_file_target_path = (
            self.encoded_raw_files_target_dir / self.original_media_file.filename
        )

        if raw_file_target_path.resolve() == self.original_media_file.path.resolve():
            logger.debug(f"Original file {self.original_media_file.filename} is already in the target raw directory. No move needed.")
            return

        if not raw_file_target_path.exists():
            try:
                shutil.move(
                    str(self.original_media_file.path), str(raw_file_target_path)
                )
                logger.debug(
                    f"Moved original file {self.original_media_file.filename} to raw archive: {raw_file_target_path}"
                )
                self.renamed_original_file = raw_file_target_path
            except shutil.Error as e:
                logger.error(
                    f"shutil.Error moving {self.original_media_file.filename} to raw archive {raw_file_target_path}: {e}"
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error moving {self.original_media_file.filename} to raw archive {raw_file_target_path}: {e}"
                )
        else:
            logger.info(
                f"Raw file {raw_file_target_path} already exists. Original {self.original_media_file.filename} not moved. Consider deleting original if identical."
            )


    def post_actions(self):
        if self.encode_info.status == JOB_STATUS_COMPLETED:
            # Remove EncodeInfo file on successful completion
            logger.debug(f"Encoding completed for {self.original_media_file.filename}. Removing progress file: {self.encode_info.path}")
            self.encode_info.remove_file()
            # Then proceed with other post actions like moving raw file
        elif self.encode_info.status == JOB_STATUS_SKIPPED:
            # Remove EncodeInfo for skipped files as well, as they don't need retry
            logger.debug(f"File {self.original_media_file.filename} was skipped. Removing progress file: {self.encode_info.path}")
            self.encode_info.remove_file()
        elif self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
            # Optionally remove for permanent errors too, or keep for investigation
            logger.debug(f"File {self.original_media_file.filename} has permanent error. Removing progress file: {self.encode_info.path}")
            self.encode_info.remove_file()


        if self.renamed_original_file and self.renamed_original_file.exists() and \
           self.renamed_original_file.resolve() != self.original_media_file.path.resolve():
            logger.debug(
                f"Post_actions: File {self.original_media_file.filename} was handled by pre-encoder (moved/renamed to {self.renamed_original_file}). Status: {self.encode_info.status}"
            )
            return

        if self.current_error_output_path and self.current_error_output_path.exists():
            logger.debug(
                f"Post_actions: File {self.original_media_file.filename} was moved to error path {self.current_error_output_path}. Status: {self.encode_info.status}"
            )
            return

        if self.encode_info.status == JOB_STATUS_COMPLETED:
            if self.args.move_raw_file:
                self._move_original_raw_file()

            final_encoded_file_path_str = self.encode_info.temp_output_path or (str(self.encoded_file) if hasattr(self, 'encoded_file') else "N/A")
            final_encoded_file = Path(final_encoded_file_path_str) if final_encoded_file_path_str != "N/A" else None

            final_encoded_size = 0
            if final_encoded_file and final_encoded_file.exists():
                final_encoded_size = final_encoded_file.stat().st_size
            elif hasattr(self, 'encoded_size'):
                final_encoded_size = self.encoded_size

            total_time_val = self.total_time if hasattr(self, 'total_time') else timedelta(0)

            size_ratio = (
                (final_encoded_size / self.original_media_file.size * 100)
                if self.original_media_file.size > 0 and final_encoded_size > 0
                else 0
            )
            logger.success(
                f"Completed: {self.original_media_file.path.relative_to(Path.cwd())}, "
                f"total time: {format_timedelta(total_time_val)}, "
                f"{formatted_size(self.original_media_file.size)} -> {formatted_size(final_encoded_size)} "
                f"({size_ratio:.0f}%) Output: {final_encoded_file_path_str}"
            )

        elif self.encode_info.status == JOB_STATUS_ERROR_RETRYABLE:
            logger.warning(
                f"Finished processing (with retryable error): {self.original_media_file.path.relative_to(Path.cwd())}. "
                f"Retries: {self.encode_info.retry_count}/{MAX_ENCODE_RETRIES}. Error: {self.encode_info.last_error_message}"
            )

        elif self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
             logger.error(
                f"Finished processing (with permanent error): {self.original_media_file.path.relative_to(Path.cwd())}. "
                f"Error: {self.encode_info.last_error_message}"
            )

        else:
            logger.info(
                f"Finished processing (status: {self.encode_info.status}): {self.original_media_file.path.relative_to(Path.cwd())}"
            )


    def _set_metadata_comment(self):
        raise NotImplementedError("Subclasses must implement _set_metadata_comment().")


class VideoEncoder(Encoder):
    pre_encoder: PreVideoEncoder

    def __init__(self, media_file: MediaFile, args: Any):
        super().__init__(media_file, args)
        self.encoded_dir = (VIDEO_OUT_DIR_ROOT / self.original_media_file.relative_dir).resolve()
        self.encoded_file = (self.encoded_dir / f"{self.original_media_file.path.stem}.mp4")
        self.success_log_output_dir = self.encoded_dir
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR / self.original_media_file.relative_dir
        ).resolve()
        self.pre_encoder = PreVideoEncoder(media_file, self.args.manual_mode, args=self.args)
        self.encode_info = self.pre_encoder.encode_info_handler
        self.video_map_cmd_part = ""
        self.audio_map_cmd_part = ""
        self.subtitle_map_cmd_part = ""


    def encode(self):
        # Ensure PreEncoder has run and its results are loaded if resuming
        # This is now handled in Encoder.start() before _perform_encode -> encode() is called.
        # self.pre_encoder attributes (like output_video_streams) should be populated.

        self.encoder_codec_name = self.encode_info.encoder
        self.crf = self.encode_info.crf

        if not self.encoder_codec_name or self.crf is None:
            error_msg = f"Pre-encoder did not set valid encoder/CRF for {self.original_media_file.filename}. Encoder: '{self.encoder_codec_name}', CRF: {self.crf}"
            logger.error(error_msg)
            self.failed_action("", error_msg, -1, is_retryable_error=False)
            raise EncodingException(error_msg)

        # Critical: Ensure self.pre_encoder has its stream attributes correctly populated.
        # This should have been done by PreEncoder.start() or Encoder.start()'s loading logic.
        if not (self.pre_encoder.output_video_streams or self.pre_encoder.output_audio_streams or self.pre_encoder.output_subtitle_streams) and \
           not (self.args and getattr(self.args, "allow_no_audio", False) and self.pre_encoder.output_video_streams): # Allow no audio if video exists
            # This check might be too late if pre_encoder_data was missing or corrupt
            # Re-check pre_encoder_data directly from encode_info if pre_encoder attributes are empty
            if self.encode_info.pre_encoder_data:
                self.pre_encoder.output_video_streams = self.encode_info.pre_encoder_data.get("output_video_streams", [])
                self.pre_encoder.output_audio_streams = self.encode_info.pre_encoder_data.get("output_audio_streams", [])
                self.pre_encoder.output_subtitle_streams = self.encode_info.pre_encoder_data.get("output_subtitle_streams", [])
                logger.debug("Restored stream selections from pre_encoder_data for building maps.")
                if not (self.pre_encoder.output_video_streams or self.pre_encoder.output_audio_streams or self.pre_encoder.output_subtitle_streams) and \
                   not (self.args and getattr(self.args, "allow_no_audio", False) and self.pre_encoder.output_video_streams):
                    error_msg = f"No processable streams selected by pre-encoder (or restored from cache) for {self.original_media_file.filename}."
                    logger.error(error_msg)
                    self.failed_action("", error_msg, -1, is_retryable_error=False) # Non-retryable config issue
                    raise EncodingException(error_msg)
            else: # No pre_encoder_data either
                error_msg = f"Pre-encoder data (including stream selections) is missing for {self.original_media_file.filename}."
                logger.error(error_msg)
                self.failed_action("", error_msg, -1, is_retryable_error=False)
                raise EncodingException(error_msg)


        self.encoded_file = (self.encoded_dir / f"{self.original_media_file.path.stem}.mp4")
        try:
            self._ffmpeg_encode_video()
        except MP4MKVEncodeFailException as e:
            logger.error(f"MP4/MKV encoding chain failed for {self.original_media_file.filename}: {e}")
            raise
        except EncodingException as e:
            logger.error(f"ffmpeg_encode_video reported EncodingException for {self.original_media_file.filename}: {e}")
            raise

    def _check_and_handle_oversized(self, attempt=1, max_attempts=3) -> bool:
        if not self.encoded_file.exists():
            logger.error(
                f"_check_and_handle_oversized: Encoded file {self.encoded_file} does not exist."
            )
            self.no_error = False
            self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message="Encoded file missing for oversized check.")
            return False

        current_encoded_size = self.encoded_file.stat().st_size
        if current_encoded_size > self.original_media_file.size:
            logger.warning(
                f"Attempt {attempt}: File is oversized. Original: {formatted_size(self.original_media_file.size)}, "
                f"Encoded: {formatted_size(current_encoded_size)} ({current_encoded_size / self.original_media_file.size:.2%} of original), "
                f"CRF: {self.crf}"
            )
            self.encoded_file.unlink(missing_ok=True)

            if attempt >= max_attempts:
                error_msg = f"Max attempts ({max_attempts}) reached for oversized file {self.original_media_file.filename}. Cannot reduce size further with CRF."
                logger.error(error_msg)
                self.no_error = False
                self.move_to_oversized_error_dir()
                self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=error_msg, crf=self.crf)
                return False

            crf_increment_val = self.crf * (MANUAL_CRF_INCREMENT_PERCENT / 100.0) if self.crf is not None else 2
            new_crf = (self.crf or MANUAL_CRF) + max(1, int(round(crf_increment_val)))


            if new_crf > MAX_CRF:
                error_msg = f"New CRF {new_crf} would exceed MAX_CRF {MAX_CRF}. Cannot re-encode {self.original_media_file.filename} to reduce size."
                logger.error(error_msg)
                self.no_error = False
                self.move_to_oversized_error_dir()
                self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=error_msg, crf=self.crf)
                return False

            self.crf = new_crf
            logger.info(
                f"Increased CRF to {self.crf} for {self.original_media_file.filename} (Oversized Attempt {attempt + 1})."
            )

            self.encode_info.dump(
                crf=self.crf,
                last_error_message=f"Oversized, retrying with CRF {self.crf} (Attempt {attempt+1})"
            )

            try:
                logger.info(
                    f"Retrying encoding for {self.original_media_file.filename} with new CRF {self.crf}."
                )
                self._ffmpeg_encode_video(
                    update_log_comment_dict={
                        "oversized_retry_attempt": attempt + 1,
                        "new_crf_for_oversized": self.crf,
                    },
                    is_oversized_retry=True,
                )
                if self.no_error:
                    return self._check_and_handle_oversized(attempt=attempt + 1, max_attempts=max_attempts)
                else:
                    logger.error(
                        f"Re-encoding attempt {attempt+1} for oversized file failed (ffmpeg error). Error handled by failed_action."
                    )
                    return False
            except Exception as e:
                logger.error(
                    f"Exception during oversized re-encode attempt {attempt+1} for {self.original_media_file.filename}: {e}"
                )
                self.no_error = False
                if self.original_media_file.path.exists() and not self.current_error_output_path:
                    self.move_to_oversized_error_dir()
                self.encode_info.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=f"Exception in oversized retry: {e}", crf=self.crf)
                return False
        else:
            logger.debug(
                f"File {self.encoded_file.name} is not oversized. Size: {formatted_size(current_encoded_size)}."
            )
            return True


    def move_to_oversized_error_dir(self):
        oversized_error_dir = (
            self.error_dir_base
            / "oversized_unfixable"
            / self.original_media_file.relative_dir
        )
        oversized_error_dir.mkdir(parents=True, exist_ok=True)
        target_error_path = oversized_error_dir / self.original_media_file.filename

        if self.original_media_file.path.exists():
            if self.original_media_file.path.resolve() != target_error_path.resolve():
                try:
                    shutil.move(
                        str(self.original_media_file.path),
                        str(target_error_path),
                    )
                    self.current_error_output_path = target_error_path
                    logger.info(
                        f"Moved unfixably oversized original file to {self.current_error_output_path}"
                    )
                except Exception as move_e:
                    logger.error(f"Could not move unfixably oversized original file {self.original_media_file.filename}: {move_e}")
        else:
            logger.warning(f"Original file {self.original_media_file.filename} not found for move_to_oversized_error_dir.")
            self.current_error_output_path = target_error_path

    def _set_metadata_comment(self, update_dic: Optional[dict] = None):
        comment_data = {
            "comment_tag": VIDEO_COMMENT_ENCODED,
            "encoder_options": {
                "codec": self.encoder_codec_name or "N/A",
                "crf": self.crf if self.crf is not None else "N/A",
                "target_vmaf": TARGET_VMAF,
                "pre_encode_manual_mode": self.pre_encoder.is_manual_mode if self.pre_encoder else "N/A",
            },
            "source_file_info": {
                "name": self.original_media_file.path.name,
                "size_bytes": self.original_media_file.size,
                "size_formatted": formatted_size(self.original_media_file.size),
                "md5": self.original_media_file.md5,
                "sha256": self.original_media_file.sha256,
            },
            "encoding_software_config": {"configured_encoders": list(VIDEO_ENCODERS_CONFIG)},
        }
        if update_dic:
            comment_data.setdefault("process_notes", {}).update(update_dic)

        self.encoded_comment_text = (
            yaml.dump(
                comment_data,
                default_flow_style=True,
                sort_keys=False,
                allow_unicode=True,
                width=99999,
            )
            .strip()
            .replace('"', '\\"')
        )

    def _ffmpeg_encode_video(
        self,
        update_log_comment_dict: Optional[dict] = None,
        is_oversized_retry: bool = False,
    ):
        self._build_ffmpeg_stream_maps()
        self._set_metadata_comment(update_log_comment_dict)
        self._build_encode_command()

        self.encode_info.dump(status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
                              ffmpeg_command=self.encode_cmd_str,
                              temp_output_path=str(self.encoded_file),
                              encoder=self.encoder_codec_name,
                              crf=self.crf)


        show_cmd_output = __debug__
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT

        res = run_cmd(
            cmd_str=self.encode_cmd_str,
            src_file_for_log=self.original_media_file.path,
            error_log_dir_for_run_cmd=self.error_dir_base,
            show_cmd=show_cmd_output,
            cmd_log_file_path=cmd_log_file_path_val,
        )

        if res and res.returncode == 0:
            logger.debug(
                f"Initial ffmpeg command successful for {self.encoded_file.name}."
            )
            self.no_error = True
            if self.keep_mtime and self.encoded_file.exists():
                try:
                    os.utime(
                        self.encoded_file,
                        times=(
                            datetime.now().timestamp(),
                            self.original_media_file.path.stat().st_mtime,
                        ),
                    )
                except Exception as utime_err:
                    logger.warning(f"Could not set mtime for {self.encoded_file.name}: {utime_err}")

            if self.encoded_file.exists():
                if not is_oversized_retry:
                    if not self._check_and_handle_oversized():
                        pass
            else:
                error_msg = f"ffmpeg reported success but output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(res.stdout, f"{res.stderr}\n{error_msg}", res.returncode, is_retryable_error=True)
                raise EncodingException(error_msg)


        elif self.encoded_file.suffix.lower() == ".mp4" and not is_oversized_retry:
            logger.warning(
                f"MP4 encoding failed for {self.original_media_file.path.name}. "
                f"Return code: {res.returncode if res else 'N/A'}. Stderr: {res.stderr if res else 'N/A'}"
                f" Retrying with .mkv container."
            )
            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)

            self.encoded_file = self.encoded_file.with_suffix(".mkv")
            self._build_ffmpeg_stream_maps()
            self._set_metadata_comment(update_log_comment_dict)
            self._build_encode_command()

            self.encode_info.dump(status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
                                  ffmpeg_command=self.encode_cmd_str,
                                  temp_output_path=str(self.encoded_file),
                                  last_error_message="MP4 failed, retrying MKV.")


            logger.info(f"Retrying with MKV: {self.encode_cmd_str}")
            res_mkv = run_cmd(
                cmd_str=self.encode_cmd_str,
                src_file_for_log=self.original_media_file.path,
                error_log_dir_for_run_cmd=self.error_dir_base,
                show_cmd=show_cmd_output,
                cmd_log_file_path=cmd_log_file_path_val,
            )

            if res_mkv and res_mkv.returncode == 0:
                logger.info(f"MKV encoding successful for {self.encoded_file.name}.")
                self.no_error = True
                if self.keep_mtime and self.encoded_file.exists():
                    try:
                        os.utime(self.encoded_file, (datetime.now().timestamp(), self.original_media_file.path.stat().st_mtime))
                    except Exception as utime_err:
                        logger.warning(f"Could not set mtime for MKV {self.encoded_file.name}: {utime_err}")

                if self.encoded_file.exists():
                    if not self._check_and_handle_oversized():
                        pass
                else:
                    error_msg_mkv = f"MKV ffmpeg reported success but output file {self.encoded_file.name} is missing."
                    logger.error(error_msg_mkv)
                    self.failed_action(res_mkv.stdout, f"{res_mkv.stderr}\n{error_msg_mkv}", res_mkv.returncode, is_retryable_error=True)
                    raise EncodingException(error_msg_mkv)

            else:
                logger.error(
                    f"MKV encoding also failed for {self.original_media_file.path.name}."
                )
                if self.encoded_file.exists():
                    self.encoded_file.unlink(missing_ok=True)

                self.failed_action(
                    res_mkv.stdout if res_mkv else (res.stdout if res else ""),
                    res_mkv.stderr if res_mkv else (res.stderr if res else ""),
                    res_mkv.returncode if res_mkv else (res.returncode if res else -1),
                    is_retryable_error=True
                )
                raise MP4MKVEncodeFailException(
                    f"Both MP4 and MKV encoding failed for {self.original_media_file.path.name}"
                )

        else:
            error_details = f"ffmpeg command failed for {self.encoded_file.name}. RC: {res.returncode if res else 'N/A'}"
            logger.error(error_details)
            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)

            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True
            )
            raise EncodingException(error_details)


    def _build_ffmpeg_stream_maps(self):
        _video_map_cmd = ""
        max_fps = 240

        if not self.pre_encoder or not hasattr(self.pre_encoder, 'output_video_streams'):
            logger.error("PreVideoEncoder instance or its stream data not available for building maps.")
            self.video_map_cmd_part = ""
            self.audio_map_cmd_part = ""
            self.subtitle_map_cmd_part = ""
            return

        video_streams_to_map = self.pre_encoder.output_video_streams
        if not video_streams_to_map:
            logger.warning(
                f"No output video streams selected by pre-encoder for {self.original_media_file.filename}. Video encoding may fail or be empty."
            )
        for video_stream in video_streams_to_map:
            fps_str = "24"
            if (
                "avg_frame_rate" in video_stream
                and video_stream["avg_frame_rate"] != "0/0"
            ):
                try:
                    fps_fraction = Fraction(str(video_stream.get("avg_frame_rate")))
                    if fps_fraction.denominator == 0: raise ValueError("Denominator is zero")
                    if fps_fraction > 0 and fps_fraction <= max_fps:
                        if fps_fraction.denominator == 1:
                            fps_str = str(fps_fraction.numerator)
                        else:
                            fps_val = float(fps_fraction)
                            fps_str = f"{fps_val:.3f}".rstrip('0').rstrip('.') if fps_val % 1 != 0 else str(int(fps_val))
                except (ZeroDivisionError, ValueError) as e:
                    logger.error(
                        f"Error parsing avg_frame_rate '{video_stream.get('avg_frame_rate')}' for {self.original_media_file.filename}: {e}. Using default FPS {fps_str}."
                    )
            else:
                logger.warning(
                    f"avg_frame_rate not found or invalid in video stream for {self.original_media_file.filename}. Using default FPS {fps_str}."
                )

            stream_index = int(video_stream.get("index", 0))
            _video_map_cmd += f'-map 0:{stream_index} -r "{fps_str}" '
        self.video_map_cmd_part = _video_map_cmd.strip()


        _audio_map_cmd = ""
        audio_output_idx = 0
        audio_streams_to_map = self.pre_encoder.output_audio_streams
        if not audio_streams_to_map:
            logger.warning(
                f"No output audio streams selected for {self.original_media_file.filename}. Output will have no audio."
            )
        for audio_stream in audio_streams_to_map:
            stream_index = int(audio_stream.get("index",0))
            channels = int(audio_stream.get("channels", 2))
            is_mkv_target = self.encoded_file.suffix.lower() == ".mkv"
            acodec_name = "copy"
            abitrate_cmd = ""
            input_codec_name = audio_stream.get("codec_name", "").lower()

            if input_codec_name in AUDIO_OPUS_CODECS and channels <= 2:
                acodec_name = OPUS_ENCODER
                max_opus_bitrate = 500 * 1000
                original_bitrate = 0
                if "bit_rate" in audio_stream:
                    try: original_bitrate = int(audio_stream.get("bit_rate"))
                    except (ValueError, TypeError): pass
                elif "tags" in audio_stream and "BPS-eng" in audio_stream["tags"]:
                    try: original_bitrate = int(audio_stream["tags"]["BPS-eng"])
                    except (ValueError, TypeError): pass

                target_opus_bitrate = max_opus_bitrate // 2
                if original_bitrate > 0:
                    target_opus_bitrate = min(original_bitrate, max_opus_bitrate)

                abitrate_cmd = f"-b:a:{audio_output_idx} {target_opus_bitrate} "
                if not is_mkv_target:
                    logger.debug(
                        f"Audio stream {stream_index} ({input_codec_name}) will be re-encoded to Opus. "
                        f"Changing output container to MKV for compatibility from {self.encoded_file.name}."
                    )
                    self.encoded_file = self.encoded_file.with_suffix(".mkv")
                    is_mkv_target = True

            _audio_map_cmd += f"-map 0:{stream_index} -c:a:{audio_output_idx} {acodec_name} {abitrate_cmd}"
            audio_output_idx += 1
        self.audio_map_cmd_part = _audio_map_cmd.strip()


        _subtitle_map_cmd = ""
        subtitle_output_idx = 0
        subtitle_streams_to_map = self.pre_encoder.output_subtitle_streams
        if not subtitle_streams_to_map:
            logger.debug(
                f"No output subtitle streams for {self.original_media_file.filename}."
            )
        for subtitle_stream in subtitle_streams_to_map:
            stream_index = int(subtitle_stream.get("index",0))
            scodec_name = "mov_text"
            input_subtitle_codec = subtitle_stream.get("codec_name", "").lower()
            is_mkv_target = self.encoded_file.suffix.lower() == ".mkv"
            if is_mkv_target:
                if input_subtitle_codec in SUBTITLE_MKV_CODECS:
                    scodec_name = "copy"
            elif input_subtitle_codec not in ["mov_text", "tx3g"]:
                logger.debug(
                    f"Subtitle stream {stream_index} (codec {input_subtitle_codec}) is not MP4-native. Will attempt conversion to mov_text for MP4."
                )
            _subtitle_map_cmd += (
                f"-map 0:{stream_index} -c:s:{subtitle_output_idx} {scodec_name} "
            )
            subtitle_output_idx += 1
        self.subtitle_map_cmd_part = _subtitle_map_cmd.strip()


    def _build_encode_command(self):
        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path.resolve()}" '
            f'-c:v "{self.encoder_codec_name}" -crf {self.crf} '
            f"{self.video_map_cmd_part} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f"{self.audio_map_cmd_part if self.audio_map_cmd_part else ''} "
            f"{self.subtitle_map_cmd_part if self.subtitle_map_cmd_part else ''} "
            f'"{self.encoded_file.resolve()}"'
        ).strip().replace("  ", " ")

        logger.debug(
            f"Built ffmpeg command for {self.original_media_file.filename}:\n{self.encode_cmd_str}"
        )


    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        video_specific_log_data = {
            "pre_encode_info": {
                "is_manual_mode": self.pre_encoder.is_manual_mode if self.pre_encoder else (self.encode_info.pre_encoder_data.get("is_manual_mode") if self.encode_info.pre_encoder_data else "N/A"),
                "estimated_size_ratio_percent": (
                    float(self.pre_encoder.best_ratio * 100)
                    if self.pre_encoder and self.pre_encoder.best_ratio is not None
                    else (float(self.encode_info.pre_encoder_data["best_ratio"] * 100) if self.encode_info.pre_encoder_data and self.encode_info.pre_encoder_data.get("best_ratio") is not None else None)
                ),
                "crf_checking_time_formatted": format_timedelta(
                    self.pre_encoder.crf_checking_time
                )
                if self.pre_encoder and self.pre_encoder.crf_checking_time
                else (format_timedelta(timedelta(seconds=self.encode_info.pre_encoder_data["crf_checking_time_seconds"])) if self.encode_info.pre_encoder_data and self.encode_info.pre_encoder_data.get("crf_checking_time_seconds") is not None else "0s"),
                "target_vmaf_for_pre_encode": TARGET_VMAF,
            },
        }
        if update_dic:
            video_specific_log_data.update(update_dic)

        super().write_success_log(
            log_date_in_filename=log_date_in_filename,
            update_dic=video_specific_log_data,
        )


class PhoneVideoEncoder(Encoder):
    def __init__(self, media_file: MediaFile, args: Any):
        super().__init__(media_file, args)
        self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()
        self.encoder_codec_name = VIDEO_CODEC_IPHONE_XR
        self.audio_encoder_codec_name = AUDIO_CODEC_IPHONE_XR
        self.cmd_options_phone = IPHONE_XR_OPTIONS
        self.success_log_output_dir = Path.cwd()
        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR
            / "phone_encoded_raw"
            / self.original_media_file.relative_dir
        ).resolve()
        encode_info_storage_dir = self.encoded_dir / ".encode_info_cache"
        encode_info_storage_dir.mkdir(parents=True, exist_ok=True)
        self.encode_info = EncodeInfo(media_file.md5, storage_dir=encode_info_storage_dir)
        if not self.encode_info.load():
            self.encode_info.dump(status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path))


    def _set_metadata_comment(self):
        comment_data = {
            "comment_tag": VIDEO_COMMENT_ENCODED,
            "encoding_profile": "iPhone_Optimized",
            "target_codec_video": self.encoder_codec_name,
            "target_codec_audio": self.audio_encoder_codec_name,
            "source_file_info": {
                "name": self.original_media_file.filename,
                "size_formatted": formatted_size(self.original_media_file.size),
                "md5": self.original_media_file.md5,
            },
        }
        self.encoded_comment_text = (
            yaml.dump(
                comment_data, default_flow_style=True, sort_keys=False, allow_unicode=True, width=99999
            ).strip().replace('"', '\\"')
        )

    def encode(self):
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)
        video_bitrate_str = f"{MANUAL_VIDEO_BIT_RATE_IPHONE_XR}"
        audio_bitrate_str = f"{MANUAL_AUDIO_BIT_RATE_IPHONE_XR}"
        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path.resolve()}" '
            f"{self.cmd_options_phone} "
            f"-c:v {self.encoder_codec_name} -b:v {video_bitrate_str}k "
            f"-c:a {self.audio_encoder_codec_name} -b:a {audio_bitrate_str} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f'"{self.encoded_file.resolve()}"'
        ).strip()
        logger.debug(f"PhoneVideoEncoder command: {self.encode_cmd_str}")
        self.encode_info.dump(status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
                              ffmpeg_command=self.encode_cmd_str,
                              temp_output_path=str(self.encoded_file),
                              encoder=self.encoder_codec_name)

        show_cmd_output = __debug__
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            cmd_str=self.encode_cmd_str,
            src_file_for_log=self.original_media_file.path,
            error_log_dir_for_run_cmd=self.error_dir_base,
            show_cmd=show_cmd_output,
            cmd_log_file_path=cmd_log_file_path_val,
        )
        if res and res.returncode == 0:
            self.no_error = True
            if self.encoded_file.exists():
                if self.keep_mtime:
                    try:
                        os.utime(self.encoded_file, (datetime.now().timestamp(), self.original_media_file.path.stat().st_mtime))
                    except Exception as utime_err:
                        logger.warning(f"Could not set mtime for phone encode {self.encoded_file.name}: {utime_err}")
                self.encoded_size = self.encoded_file.stat().st_size
            else:
                error_msg = f"Phone encode: ffmpeg reported success but output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(res.stdout, f"{res.stderr}\n{error_msg}", res.returncode, is_retryable_error=True)
                raise EncodingException(error_msg)
        else:
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True
            )
            raise EncodingException(f"PhoneVideoEncoding failed for {self.original_media_file.filename}")


class AudioEncoder(Encoder):
    def __init__(
        self,
        media_file: MediaFile,
        target_bit_rate: int = TARGET_BIT_RATE_IPHONE_XR,
        args: Optional[Any] = None,
    ):
        super().__init__(media_file=media_file, args=args)
        self.encoder_codec_name = DEFAULT_AUDIO_ENCODER
        self.target_bit_rate = target_bit_rate
        self.encoded_dir = (
            AUDIO_ENCODED_ROOT_DIR / self.original_media_file.relative_dir
        ).resolve()
        self.encoded_file = (
            self.encoded_dir
            / self.original_media_file.path.with_suffix(self._get_file_extension()).name
        )
        self.success_log_output_dir = self.encoded_dir
        self.encoded_raw_files_target_dir = (
            AUDIO_ENCODED_RAW_DIR / self.original_media_file.relative_dir
        ).resolve()
        encode_info_storage_dir = self.encoded_dir / ".encode_info_cache"
        encode_info_storage_dir.mkdir(parents=True, exist_ok=True)
        self.encode_info = EncodeInfo(media_file.md5, storage_dir=encode_info_storage_dir)
        if not self.encode_info.load():
            self.encode_info.dump(status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path))


    def _get_file_extension(self) -> str:
        if self.encoder_codec_name == "libopus":
            return ".opus"
        elif self.encoder_codec_name == "libmp3lame":
            return ".mp3"
        else:
            logger.warning(
                f"Unknown audio encoder '{self.encoder_codec_name}' for extension. Defaulting to .audio"
            )
            return ".audio"

    def _set_metadata_comment(self):
        comment_data = {
            "comment_tag": AUDIO_COMMENT_ENCODED,
            "encoder_profile": "AudioOptimized",
            "target_codec": self.encoder_codec_name,
            "target_bitrate_bps": self.target_bit_rate,
            "source_file_info": {
                "name": self.original_media_file.filename,
                "size_formatted": formatted_size(self.original_media_file.size),
                "md5": self.original_media_file.md5,
            },
        }
        self.encoded_comment_text = (
            yaml.dump(
                comment_data, default_flow_style=True, sort_keys=False, allow_unicode=True, width=99999
            ).strip().replace('"', '\\"')
        )

    def encode(self):
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)
        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path.resolve()}" '
            f"-vn -map 0:a "
            f"-c:a {self.encoder_codec_name} "
            f"-b:a {self.target_bit_rate} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f'"{self.encoded_file.resolve()}"'
        ).strip()
        logger.debug(f"AudioEncoder command: {self.encode_cmd_str}")
        self.encode_info.dump(status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
                              ffmpeg_command=self.encode_cmd_str,
                              temp_output_path=str(self.encoded_file),
                              encoder=self.encoder_codec_name)

        show_cmd_output = __debug__
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            cmd_str=self.encode_cmd_str,
            src_file_for_log=self.original_media_file.path,
            error_log_dir_for_run_cmd=self.error_dir_base,
            show_cmd=show_cmd_output,
            cmd_log_file_path=cmd_log_file_path_val,
        )
        if res and res.returncode == 0:
            self.no_error = True
            if self.encoded_file.exists():
                if self.keep_mtime:
                    try:
                        os.utime(self.encoded_file, (datetime.now().timestamp(), self.original_media_file.path.stat().st_mtime))
                    except Exception as utime_err:
                        logger.warning(f"Could not set mtime for audio encode {self.encoded_file.name}: {utime_err}")
                self.encoded_size = self.encoded_file.stat().st_size
            else:
                error_msg = f"Audio encode: ffmpeg reported success but output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(res.stdout, f"{res.stderr}\n{error_msg}", res.returncode, is_retryable_error=True)
                raise EncodingException(error_msg)
        else:
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True
            )
            raise EncodingException(f"AudioEncoding failed for {self.original_media_file.filename}")