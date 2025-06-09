import os
import platform
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Optional

import shlex
import yaml
from loguru import logger

from ..config.common import (
    BASE_ERROR_DIR,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_ERROR_PERMANENT,
    JOB_STATUS_ERROR_RETRYABLE,
    JOB_STATUS_PENDING,
    JOB_STATUS_PREPROCESSING_DONE,
    JOB_STATUS_SKIPPED,
    MAX_ENCODE_RETRIES,
)
from ..domain.exceptions import EncodingException, PreprocessingException, SkippedFileException
from ..domain.media import MediaFile
from ..domain.temp_models import EncodeInfo
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import format_timedelta, formatted_size
from .logging_service import ErrorLog, SuccessLog
from .preprocessing_service import PreEncoder


class Encoder:
    pre_encoder: Optional[PreEncoder] = None
    encode_start_datetime: datetime
    encode_end_datetime: datetime
    encode_time: timedelta
    total_time: timedelta
    encoder_codec_name: str
    crf: Optional[int] = None
    encoded_dir: Path
    encoded_root_dir: Path
    encoded_file: Path
    encoded_size: int
    renamed_original_file: Optional[Path] = None
    success_log: Optional[SuccessLog] = None
    encode_info: EncodeInfo
    # encode_cmd_str をリストに変更
    encode_cmd_list: List[str] = []

    def __init__(self, media_file: MediaFile, args: Any):
        self.original_media_file = media_file
        self.args = args
        self.no_error = True

        self.error_dir_base = BASE_ERROR_DIR.resolve()
        self.current_error_output_path: Optional[Path] = None

        self.success_log_dir_base = Path.cwd()
        self.encoded_comment_text = ""
        self.encode_cmd_list = []  # 初期化
        self.encoded_raw_files_target_dir: Optional[Path] = None

        self.keep_mtime = args.keep_mtime if hasattr(args, "keep_mtime") else False

    def start(self):
        if not hasattr(self, "encode_info") or not self.encode_info:
            logger.warning(
                f"EncodeInfo not set for {self.original_media_file.filename} before Encoder.start(). Initializing a default one."
            )
            default_storage_dir = (
                Path.cwd() / ".smart_encoder_cache" / self.original_media_file.md5[:2]
            )
            default_storage_dir.mkdir(parents=True, exist_ok=True)
            self.encode_info = EncodeInfo(
                self.original_media_file.md5, storage_dir=default_storage_dir
            )
            if not self.encode_info.load():
                self.encode_info.dump(
                    status=JOB_STATUS_PENDING,
                    ori_video_path=str(self.original_media_file.path),
                )

        logger.debug(
            f"Encoder.start() for {self.original_media_file.path.name}, original_media_file.path = '{self.original_media_file.path}' (Status: {self.encode_info.status})"
        )

        if not hasattr(self, "encoded_dir") or not self.encoded_dir:
            logger.error(
                f"encoded_dir not set for {self.original_media_file.filename} before start() call."
            )
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_PERMANENT,
                last_error_message="Internal error: encoded_dir not set.",
            )
            self.no_error = False
            self.post_actions()
            return
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        if self.encode_info.status == JOB_STATUS_COMPLETED:
            logger.info(
                f"File {self.original_media_file.filename} already completed. Finalizing."
            )
            self.no_error = True
            if self.encode_info.temp_output_path:
                self.encoded_file = Path(self.encode_info.temp_output_path)
                if self.encoded_file.exists():
                    self.encoded_size = self.encoded_file.stat().st_size
            if not hasattr(self, "encode_time"):
                self.encode_time = timedelta(0)
            if not hasattr(self, "total_time"):
                self.total_time = timedelta(0)
            if not hasattr(self, "encode_end_datetime"):
                self.encode_end_datetime = (
                    datetime.fromisoformat(self.encode_info.last_updated)
                    if self.encode_info.last_updated
                    else datetime.now()
                )
            self.write_success_log()
            self.post_actions()
            return

        if self.encode_info.status == JOB_STATUS_SKIPPED:
            logger.info(
                f"File {self.original_media_file.filename} was previously skipped. Finalizing."
            )
            self.no_error = True
            if self.pre_encoder and self.pre_encoder.renamed_file_on_skip_or_error:
                self.renamed_original_file = (
                    self.pre_encoder.renamed_file_on_skip_or_error
                )
            self.post_actions()
            return

        if self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
            logger.error(
                f"File {self.original_media_file.filename} has a permanent error. Not processing."
            )
            self.no_error = False
            self.post_actions()
            return

        if self.pre_encoder:
            try:
                if self.encode_info.status not in [
                    JOB_STATUS_PREPROCESSING_DONE,
                    JOB_STATUS_ENCODING_FFMPEG_STARTED,
                ]:
                    logger.debug(
                        f"Pre-encoder required for {self.original_media_file.filename}, current status: {self.encode_info.status}. Running pre_encoder.start()."
                    )
                    self.pre_encoder.start()
                else:
                    logger.debug(
                        f"Pre-encoder stage already completed or bypassed for {self.original_media_file.filename} (status: {self.encode_info.status}). Loading results for encoding."
                    )
                    if (
                        self.encode_info.status == JOB_STATUS_PREPROCESSING_DONE
                        or self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED
                    ):
                        if self.encode_info.pre_encoder_data:
                            self.pre_encoder.best_encoder = (
                                self.encode_info.encoder
                                or self.encode_info.pre_encoder_data.get(
                                    "best_encoder", ""
                                )
                            )
                            self.pre_encoder.best_crf = (
                                self.encode_info.crf
                                if self.encode_info.crf is not None
                                else self.encode_info.pre_encoder_data.get(
                                    "best_crf", 0
                                )
                            )
                            self.pre_encoder.output_video_streams = (
                                self.encode_info.pre_encoder_data.get(
                                    "output_video_streams", []
                                )
                            )
                            self.pre_encoder.output_audio_streams = (
                                self.encode_info.pre_encoder_data.get(
                                    "output_audio_streams", []
                                )
                            )
                            self.pre_encoder.output_subtitle_streams = (
                                self.encode_info.pre_encoder_data.get(
                                    "output_subtitle_streams", []
                                )
                            )
                            self.pre_encoder.is_manual_mode = (
                                self.encode_info.pre_encoder_data.get(
                                    "is_manual_mode", self.pre_encoder.is_manual_mode
                                )
                            )
                            self.pre_encoder.best_ratio = (
                                self.encode_info.pre_encoder_data.get("best_ratio")
                            )
                            time_sec = self.encode_info.pre_encoder_data.get(
                                "crf_checking_time_seconds"
                            )
                            if time_sec is not None:
                                self.pre_encoder.crf_checking_time = timedelta(
                                    seconds=time_sec
                                )
                        else:  # This case should ideally not be hit if status implies pre_encoder_data should exist
                            logger.warning(
                                f"Status is {self.encode_info.status} but no pre_encoder_data found in EncodeInfo for {self.original_media_file.filename}. Pre-encoder dependent steps might fail."
                            )

                if (
                    self.pre_encoder.renamed_file_on_skip_or_error
                    and self.pre_encoder.renamed_file_on_skip_or_error.exists()
                    and self.pre_encoder.renamed_file_on_skip_or_error.resolve()
                    != self.original_media_file.path.resolve()
                ):
                    self.renamed_original_file = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                    self.no_error = self.encode_info.status == JOB_STATUS_SKIPPED
                    self.post_actions()
                    return

                # Ensure encoder_codec_name and crf are set from EncodeInfo if pre-processing is done.
                if self.encode_info.status == JOB_STATUS_PREPROCESSING_DONE:
                    self.encoder_codec_name = self.encode_info.encoder
                    self.crf = self.encode_info.crf
                elif (
                    self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED
                ):  # Resuming ffmpeg
                    self.encoder_codec_name = self.encode_info.encoder
                    self.crf = self.encode_info.crf

            except SkippedFileException as e:
                logger.info(
                    f"Skipped by pre-encoder: {self.original_media_file.filename} - {e}"
                )
                if self.pre_encoder.renamed_file_on_skip_or_error:
                    self.renamed_original_file = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                self.no_error = True
                self.post_actions()
                return
            except PreprocessingException as pe:
                logger.error(
                    f"Pre-processing error for {self.original_media_file.filename}: {pe}"
                )
                self.no_error = False
                if self.pre_encoder.renamed_file_on_skip_or_error:
                    self.current_error_output_path = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                self.post_actions()
                return
            except Exception as pe_general:
                logger.error(
                    f"Unexpected error during pre-encoding for {self.original_media_file.filename}: {pe_general}",
                    exc_info=True,
                )
                self.no_error = False
                if self.pre_encoder and self.pre_encoder.renamed_file_on_skip_or_error:
                    self.current_error_output_path = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                self.post_actions()
                return

        if self.no_error and self.encode_info.status not in [
            JOB_STATUS_COMPLETED,
            JOB_STATUS_SKIPPED,
            JOB_STATUS_ERROR_PERMANENT,
        ]:
            self._perform_encode()

        self.post_actions()

    def _perform_encode(self):
        if self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED or (
            self.encode_info.status == JOB_STATUS_ERROR_RETRYABLE
            and self.encode_info.ffmpeg_command
        ):
            action = (
                "Resuming"
                if self.encode_info.status == JOB_STATUS_ENCODING_FFMPEG_STARTED
                else f"Retrying (Attempt {self.encode_info.retry_count + 1})"
            )
            logger.info(f"{action} encoding for {self.original_media_file.filename}.")

            if self.encode_info.temp_output_path:
                temp_file_to_delete = Path(self.encode_info.temp_output_path)
                if temp_file_to_delete.exists():
                    logger.debug(
                        f"Deleting potentially incomplete previous output file: {temp_file_to_delete}"
                    )
                    try:
                        temp_file_to_delete.unlink(missing_ok=True)
                    except OSError as e:
                        logger.error(f"Could not delete {temp_file_to_delete}: {e}")
                        self.encode_info.dump(
                            status=JOB_STATUS_ERROR_PERMANENT,
                            last_error_message=f"Failed to delete temp file: {e}",
                        )
                        self.no_error = False
                        return

        self.encode_start_datetime = datetime.now()
        logger.info(f"Encoding started for: {self.original_media_file.path.name}")
        self.no_error = True

        try:
            self.encode()
        except EncodingException as enc_ex:
            logger.error(
                f"EncodingException caught for {self.original_media_file.filename}: {enc_ex}"
            )
            return
        except Exception as general_enc_ex:
            logger.error(
                f"Unexpected exception during encode() method for {self.original_media_file.filename}: {general_enc_ex}",
                exc_info=True,
            )
            self.no_error = False
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_RETRYABLE,
                last_error_message=f"Unexpected encode error: {general_enc_ex}",
                increment_retry_count=True,
            )
            return

        if self.no_error:
            self.encode_end_datetime = datetime.now()
            self.encode_time = self.encode_end_datetime - self.encode_start_datetime

            pre_encode_time = timedelta(0)
            if self.pre_encoder and self.pre_encoder.crf_checking_time:
                pre_encode_time = self.pre_encoder.crf_checking_time
            elif (
                self.encode_info.pre_encoder_data
                and self.encode_info.pre_encoder_data.get("crf_checking_time_seconds")
                is not None
            ):
                pre_encode_time = timedelta(
                    seconds=self.encode_info.pre_encoder_data[
                        "crf_checking_time_seconds"
                    ]
                )

            self.total_time = pre_encode_time + self.encode_time

            self.encode_info.dump(
                status=JOB_STATUS_COMPLETED, temp_output_path=str(self.encoded_file)
            )
            self.write_success_log()
        else:
            logger.warning(
                f"Encoding operation for {self.original_media_file.filename} finished with no_error=False. Status: {self.encode_info.status}"
            )

    def failed_action(
        self,
        res_stdout: str,
        res_stderr: str,
        return_code: int,
        is_retryable_error: bool = True,
    ):
        self.no_error = False
        error_message_short = f"ffmpeg failed (rc={return_code})"
        error_message_full = (
            f"ffmpeg command failed for {self.original_media_file.path}, encoder: {getattr(self, 'encoder_codec_name', 'N/A')} "
            f"return code: ({return_code}):\nSTDOUT: {res_stdout}\nSTDERR: {res_stderr}"
        )
        logger.error(error_message_full)

        error_file_dir = (
            self.error_dir_base
            / f"ffmpeg_rc_{return_code}"
            / self.original_media_file.relative_dir
        )
        error_file_dir.mkdir(parents=True, exist_ok=True)
        error_log = ErrorLog(error_file_dir)
        # コマンドリストを文字列に変換してログに記録
        cmd_str_for_log = (
            " ".join(self.encode_cmd_list) if self.encode_cmd_list else "N/A"
        )
        try:
            cmd_str_for_log = (
                subprocess.list2cmdline(self.encode_cmd_list)
                if os.name == "nt" and self.encode_cmd_list
                else " ".join(shlex.quote(s) for s in self.encode_cmd_list)
            )
        except Exception:
            cmd_str_for_log = " ".join(map(str, self.encode_cmd_list))

        error_log.write(
            f"Failed command: {cmd_str_for_log}",
            f"Original file: {str(self.original_media_file.path)}",
            f"MD5: {self.original_media_file.md5}",
            f"Probe info (brief): Video streams: {len(self.original_media_file.video_streams)}, Audio: {len(self.original_media_file.audio_streams)}",
            f"Stdout: {res_stdout}",
            f"Stderr: {res_stderr}",
        )

        if is_retryable_error and self.encode_info.retry_count < MAX_ENCODE_RETRIES:
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_RETRYABLE,
                last_error_message=error_message_short,
                increment_retry_count=True,
            )
            logger.warning(
                f"Marked {self.original_media_file.filename} as retryable. Retry {self.encode_info.retry_count}/{MAX_ENCODE_RETRIES}."
            )
        else:
            final_error_reason = error_message_short
            if not is_retryable_error:
                logger.error(
                    f"Non-retryable ffmpeg error for {self.original_media_file.filename}."
                )
            else:
                final_error_reason = f"Max retries ({MAX_ENCODE_RETRIES}) reached. Last error: {error_message_short}"
                logger.error(final_error_reason)

            self.encode_info.dump(
                status=JOB_STATUS_ERROR_PERMANENT, last_error_message=final_error_reason
            )

            perm_error_dir = (
                self.error_dir_base
                / "ffmpeg_permanent_error"
                / self.original_media_file.relative_dir
            )
            perm_error_dir.mkdir(parents=True, exist_ok=True)
            target_error_path = perm_error_dir / self.original_media_file.filename

            if self.original_media_file.path.exists():
                if (
                    self.original_media_file.path.resolve()
                    != target_error_path.resolve()
                ):
                    try:
                        shutil.move(
                            str(self.original_media_file.path), str(target_error_path)
                        )
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

        if (
            hasattr(self, "encoded_file")
            and self.encoded_file
            and self.encoded_file.exists()
        ):
            try:
                self.encoded_file.unlink(missing_ok=True)
                logger.debug(f"Deleted partially encoded file: {self.encoded_file}")
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

        if (
            not hasattr(self, "encoded_file")
            or not self.encoded_file
            or not self.encoded_file.exists()
        ):
            logger.error(
                f"Cannot write success log for {self.original_media_file.filename}: encoded file missing at {getattr(self, 'encoded_file', 'N/A')}"
            )
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_PERMANENT,
                last_error_message="Encoded file missing after completion.",
            )
            return

        try:
            if not hasattr(self, "encoded_size") or self.encoded_size == 0:
                self.encoded_size = self.encoded_file.stat().st_size
        except FileNotFoundError:
            logger.error(
                f"Success log: Encoded file {self.encoded_file} not found for size check."
            )
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_PERMANENT,
                last_error_message="Encoded file not found for success log.",
            )
            return

        if not hasattr(self, "encode_end_datetime"):
            self.encode_end_datetime = (
                datetime.fromisoformat(self.encode_info.last_updated)
                if self.encode_info.last_updated
                else datetime.now()
            )
        if not hasattr(self, "total_time"):
            self.total_time = timedelta(0)
        if not hasattr(self, "encode_time"):
            self.encode_time = timedelta(0)

        log_dict = {
            "index": 0,
            "input_file": str(self.original_media_file.path),
            "source_file_md5": self.original_media_file.md5,
            "source_file_sha256": self.original_media_file.sha256,
            "encoder_codec": getattr(
                self, "encoder_codec_name", self.encode_info.encoder or "N/A"
            ),
            "crf": getattr(
                self,
                "crf",
                self.encode_info.crf if self.encode_info.crf is not None else "N/A",
            ),
            "file_duration_seconds": self.original_media_file.duration,
            "file_duration_formatted": format_timedelta(
                timedelta(seconds=self.original_media_file.duration)
            ),
            "total_elapsed_time_formatted": format_timedelta(self.total_time),
            "encode_time_formatted": format_timedelta(self.encode_time),
            "encode_efficiency_factor": round(
                self.encode_time.total_seconds() / self.original_media_file.duration, 2
            )
            if self.original_media_file.duration > 0
            and self.encode_time.total_seconds() > 0
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
            logger.debug(
                f"Skipping move of raw file for {self.original_media_file.filename} as job status is '{self.encode_info.status}'."
            )
            return

        if not self.encoded_raw_files_target_dir:
            logger.debug(
                f"Raw file target directory not set for {self.original_media_file.filename}, skipping move."
            )
            return

        if not self.original_media_file.path.exists():
            if (
                self.renamed_original_file
                and self.renamed_original_file.is_file()
                and self.renamed_original_file.parent.resolve()
                == self.encoded_raw_files_target_dir.resolve()
            ):
                logger.debug(
                    f"Original file {self.original_media_file.filename} seems already in raw archive: {self.renamed_original_file}"
                )
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
            logger.debug(
                f"Original file {self.original_media_file.filename} is already in the target raw directory. No move needed."
            )
            return

        if raw_file_target_path.exists():
            try:
                original_size = self.original_media_file.path.stat().st_size
                existing_raw_size = raw_file_target_path.stat().st_size

                if original_size > existing_raw_size:
                    logger.info(
                        f"Original file {self.original_media_file.filename} ({formatted_size(original_size)}) is larger than existing raw file {raw_file_target_path.name} ({formatted_size(existing_raw_size)}). Overwriting."
                    )
                    raw_file_target_path.unlink()
                    shutil.move(
                        str(self.original_media_file.path), str(raw_file_target_path)
                    )
                    logger.debug(
                        f"Overwrote raw archive with larger original file: {raw_file_target_path}"
                    )
                    self.renamed_original_file = raw_file_target_path
                else:
                    logger.info(
                        f"Original file {self.original_media_file.filename} ({formatted_size(original_size)}) is not larger than existing raw file {raw_file_target_path.name} ({formatted_size(existing_raw_size)}). Deleting original."
                    )
                    self.original_media_file.path.unlink()
                    self.renamed_original_file = raw_file_target_path
            except FileNotFoundError:
                logger.warning(
                    f"File not found during size comparison or deletion in _move_original_raw_file for {self.original_media_file.filename}."
                )
            except Exception as e:
                logger.error(
                    f"Error during raw file comparison/move for {self.original_media_file.filename}: {e}"
                )
        else:
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

    def post_actions(self):
        if self.encode_info.status == JOB_STATUS_COMPLETED:
            logger.debug(
                f"Encoding completed for {self.original_media_file.filename}. Removing progress file: {self.encode_info.path}"
            )
            self.encode_info.remove_file()
        elif self.encode_info.status == JOB_STATUS_SKIPPED:
            logger.debug(
                f"File {self.original_media_file.filename} was skipped. Removing progress file: {self.encode_info.path}"
            )
            self.encode_info.remove_file()
        elif self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
            logger.debug(
                f"File {self.original_media_file.filename} has permanent error. Removing progress file: {self.encode_info.path}"
            )
            self.encode_info.remove_file()

        if (
            self.renamed_original_file
            and self.renamed_original_file.exists()
            and self.renamed_original_file.resolve()
            != self.original_media_file.path.resolve()
        ):
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
            final_encoded_file_path_str = self.encode_info.temp_output_path or (
                str(self.encoded_file)
                if hasattr(self, "encoded_file") and self.encoded_file
                else "N/A"
            )
            final_encoded_file = (
                Path(final_encoded_file_path_str)
                if final_encoded_file_path_str != "N/A"
                else None
            )
            final_encoded_size = 0
            if final_encoded_file and final_encoded_file.exists():
                final_encoded_size = final_encoded_file.stat().st_size
            elif hasattr(self, "encoded_size"):
                final_encoded_size = self.encoded_size
            total_time_val = (
                self.total_time if hasattr(self, "total_time") else timedelta(0)
            )
            size_ratio = (
                (final_encoded_size / self.original_media_file.size * 100)
                if self.original_media_file.size > 0 and final_encoded_size > 0
                else 0
            )
            logger.success(
                f"Completed: {self.original_media_file.path.name}, "
                f"time: {format_timedelta(total_time_val)}, "
                f"{formatted_size(self.original_media_file.size)} -> {formatted_size(final_encoded_size)} "
                f"({size_ratio:.0f}%) Output: {final_encoded_file.name if final_encoded_file else 'N/A'}"
            )
        elif self.encode_info.status == JOB_STATUS_ERROR_RETRYABLE:
            logger.warning(
                f"Finished {self.original_media_file.path.name} (retryable error). "
                f"Retries: {self.encode_info.retry_count}/{MAX_ENCODE_RETRIES}. Error: {self.encode_info.last_error_message}"
            )
        elif self.encode_info.status == JOB_STATUS_ERROR_PERMANENT:
            logger.error(
                f"Finished {self.original_media_file.path.name} (permanent error). "
                f"Error: {self.encode_info.last_error_message}"
            )
        else:
            logger.info(
                f"Finished {self.original_media_file.path.name} (status: {self.encode_info.status})"
            )

    def _set_metadata_comment(self):
        raise NotImplementedError("Subclasses must implement _set_metadata_comment().")