import os
import platform
import shutil
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from pprint import pformat
from typing import Optional

import yaml
from loguru import logger

# Utils
from ..utils.format_utils import format_timedelta, formatted_size
from ..utils.ffmpeg_utils import run_cmd

# Domain
from ..domain.media import MediaFile
from ..domain.exceptions import (
    SkippedFileException,
    MP4MKVEncodeFailException,
    EncodingException,
    PreprocessingException,
    FormatExcludedException,
    NoAudioStreamException,  # Still import if PreprocessingException might wrap it
)

# Services
from .logging_service import ErrorLog, SuccessLog
from .preprocessing_service import PreVideoEncoder, PreEncoder

# Config
from ..config.audio import (
    DEFAULT_AUDIO_ENCODER,
    TARGET_BIT_RATE_IPHONE_XR,
    AUDIO_ENCODED_ROOT_DIR,
    AUDIO_ENCODED_RAW_DIR,
    AUDIO_COMMENT_ENCODED,
)
from ..config.common import COMMAND_TEXT, BASE_ERROR_DIR
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

    def __init__(self, media_file: MediaFile, args):
        self.original_media_file = media_file
        self.args = args  # Store args to be passed to PreEncoder
        self.no_error = True

        self.error_dir_base = BASE_ERROR_DIR.resolve()
        self.current_error_output_path: Optional[Path] = None

        self.success_log_dir_base = Path.cwd()
        self.encoded_comment_text = ""
        self.encode_cmd_str = ""
        self.encoded_raw_files_target_dir: Optional[Path] = None

        self.keep_mtime = args.keep_mtime if hasattr(args, "keep_mtime") else False

    def start(self):
        logger.info(
            f"Starting processing for: {self.original_media_file.path.relative_to(Path.cwd())}"
        )
        if not hasattr(self, "encoded_dir") or not self.encoded_dir:
            logger.error("encoded_dir not set before start() call.")
            self.no_error = False
            return
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(
            f"Encoder initial state for {self.original_media_file.filename}: {pformat(vars(self))}"
        )

        if self.pre_encoder:
            try:
                logger.debug(
                    f"Starting pre-encoder for {self.original_media_file.filename}"
                )
                # Pass args to PreEncoder instance if it needs them (e.g., for --allow-no-audio)
                # This is better done in PreEncoder's __init__ if args are passed there.
                # Assuming PreEncoder (and its subclasses) got args in their constructor.
                self.pre_encoder.start()
                if (
                    self.pre_encoder.renamed_file_on_skip_or_error
                    and self.pre_encoder.renamed_file_on_skip_or_error.exists()
                    and self.pre_encoder.renamed_file_on_skip_or_error
                    != self.original_media_file.path
                ):
                    self.renamed_original_file = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                    self.post_actions()
                    return
            except FormatExcludedException as fee:
                logger.warning(
                    f"Skipped due to excluded format: {self.original_media_file.filename} - {fee}"
                )
                if (
                    hasattr(self.pre_encoder, "renamed_file_on_skip_or_error")
                    and self.pre_encoder.renamed_file_on_skip_or_error
                ):
                    self.renamed_original_file = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                self.post_actions()
                return
            except SkippedFileException as e:
                logger.info(
                    f"Skipped during pre-encoding: {self.original_media_file.filename} - {e}"
                )
                if (
                    hasattr(self.pre_encoder, "renamed_file_on_skip_or_error")
                    and self.pre_encoder.renamed_file_on_skip_or_error
                ):
                    self.renamed_original_file = (
                        self.pre_encoder.renamed_file_on_skip_or_error
                    )
                self.post_actions()
                return
            # NoAudioStreamException, if it needs to halt processing (i.e., --allow-no-audio is false),
            # will be re-raised from PreVideoEncoder and caught as PreprocessingException here.
            except PreprocessingException as pe:
                logger.error(
                    f"Pre-processing error for {self.original_media_file.filename}: {pe}",
                    exc_info=False,
                )  # Set exc_info based on verbosity
                self.no_error = False
                if self.pre_encoder and self.pre_encoder.renamed_file_on_skip_or_error:
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
                if (
                    self.pre_encoder
                    and hasattr(self.pre_encoder, "move_file_to_error_dir")
                    and not (
                        self.pre_encoder.renamed_file_on_skip_or_error
                        and self.pre_encoder.renamed_file_on_skip_or_error.exists()
                    )
                ):
                    self.pre_encoder.move_file_to_error_dir(
                        error_subdir_name=f"pre_encode_unexpected_error_{type(pe_general).__name__}"
                    )
                    if self.pre_encoder.renamed_file_on_skip_or_error:
                        self.current_error_output_path = (
                            self.pre_encoder.renamed_file_on_skip_or_error
                        )
                self.post_actions()
                return

        if self.no_error:
            self._perform_encode()

        self.post_actions()

    def _perform_encode(self):
        self.encode_start_datetime = datetime.now()
        logger.info(
            f"Encoding: {self.original_media_file.path.relative_to(Path.cwd())}"
        )

        try:
            self.encode()
        except Exception as enc_ex:
            logger.error(
                f"Exception during encode() method for {self.original_media_file.filename}: {enc_ex}",
                exc_info=True,
            )
            self.no_error = False
            if (
                not self.current_error_output_path
                and self.original_media_file.path.exists()
            ):
                generic_error_dir = (
                    self.error_dir_base
                    / "encode_method_error"
                    / self.original_media_file.relative_dir
                )
                generic_error_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(
                        str(self.original_media_file.path),
                        generic_error_dir / self.original_media_file.filename,
                    )
                    self.current_error_output_path = (
                        generic_error_dir / self.original_media_file.filename
                    )
                    logger.info(
                        f"Moved {self.original_media_file.filename} to error dir due to encode() exception."
                    )
                except Exception as move_e:
                    logger.error(
                        f"Could not move {self.original_media_file.filename} after encode() exception: {move_e}"
                    )
            return

        self.encode_end_datetime = datetime.now()
        self.encode_time = self.encode_end_datetime - self.encode_start_datetime

        pre_encode_time = timedelta(0)
        if (
            self.pre_encoder
            and hasattr(self.pre_encoder, "crf_checking_time")
            and self.pre_encoder.crf_checking_time
        ):
            pre_encode_time = self.pre_encoder.crf_checking_time

        self.total_time = pre_encode_time + self.encode_time

        if self.no_error:
            self.write_success_log()
        else:
            logger.warning(
                f"Skipping success log for {self.original_media_file.filename} due to encoding errors."
            )

    def failed_action(self, res_stdout: str, res_stderr: str, return_code: int):
        self.no_error = False
        logger.error(
            f"ffmpeg command failed for {self.original_media_file.path}, encoder: {getattr(self, 'encoder_codec_name', 'N/A')} "
            f"return code: ({return_code}):\nSTDOUT: {res_stdout}\nSTDERR: {res_stderr}"
        )

        error_file_dir = (
            self.error_dir_base
            / str(return_code)
            / self.original_media_file.relative_dir
        )
        error_file_dir.mkdir(parents=True, exist_ok=True)

        error_log = ErrorLog(error_file_dir)
        error_log.write(
            f"Failed command: {self.encode_cmd_str}",
            f"Original file: {str(self.original_media_file.path)}",
            f"Probe info: {str(self.original_media_file.probe)}",
            f"Stdout: {res_stdout}",
            f"Stderr: {res_stderr}",
        )

        target_error_path = error_file_dir / self.original_media_file.filename
        if self.original_media_file.path.exists():
            try:
                shutil.move(str(self.original_media_file.path), str(target_error_path))
                logger.info(
                    f"Moved original file {self.original_media_file.filename} to {target_error_path}"
                )
                self.current_error_output_path = target_error_path
            except Exception as e:
                logger.error(
                    f"Could not move original file {self.original_media_file.filename} to error dir: {e}"
                )
        else:
            logger.warning(
                f"Original file {self.original_media_file.path} not found for moving to error dir."
            )

        if (
            hasattr(self, "encoded_file")
            and self.encoded_file
            and self.encoded_file.exists()
        ):
            try:
                self.encoded_file.unlink()
                logger.info(f"Deleted partially encoded file: {self.encoded_file}")
            except OSError as e:
                logger.error(
                    f"Could not delete partially encoded file {self.encoded_file}: {e}"
                )

    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        if not self.no_error or self.renamed_original_file:
            logger.debug(
                f"Skipping success log for {self.original_media_file.filename}: no_error={self.no_error}, renamed_original_file existence is {self.renamed_original_file is not None}"
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
            return

        try:
            self.encoded_size = self.encoded_file.stat().st_size
        except FileNotFoundError:
            logger.error(
                f"Success log: Encoded file {self.encoded_file} not found for size check."
            )
            return

        log_dict = {
            "index": 0,
            "input_file": str(self.original_media_file.path),
            "source_file_md5": self.original_media_file.md5,
            "source_file_sha256": self.original_media_file.sha256,
            "encoder_codec": getattr(self, "encoder_codec_name", "N/A"),
            "crf": getattr(self, "crf", "N/A"),
            "file_duration_seconds": self.original_media_file.duration,
            "file_duration_formatted": format_timedelta(
                timedelta(seconds=self.original_media_file.duration)
            ),
            "total_elapsed_time_formatted": format_timedelta(
                self.total_time if hasattr(self, "total_time") else timedelta(0)
            ),
            "encode_time_formatted": format_timedelta(
                self.encode_time if hasattr(self, "encode_time") else timedelta(0)
            ),
            "encode_efficiency_factor": round(
                self.encode_time.total_seconds() / self.original_media_file.duration, 2
            )
            if self.original_media_file.duration > 0 and hasattr(self, "encode_time")
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
            "ended_datetime": self.encode_end_datetime.strftime("%Y%m%d_%H:%M:%S")
            if hasattr(self, "encode_end_datetime")
            else "N/A",
            "encoded_file_path": str(self.encoded_file),
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
        logger.info(
            f"Success log written for {self.original_media_file.filename} to {self.success_log.log_file_path}"
        )

    def encode(self):
        raise NotImplementedError("Subclasses must implement the encode() method.")

    def _move_original_raw_file(self):
        if not self.encoded_raw_files_target_dir:
            logger.debug(
                f"Raw file target directory not set for {self.original_media_file.filename}, skipping move."
            )
            return

        if not self.original_media_file.path.exists():
            logger.warning(
                f"Original file {self.original_media_file.path} does not exist, cannot move to raw archive."
            )
            return

        self.encoded_raw_files_target_dir.mkdir(parents=True, exist_ok=True)
        raw_file_target_path = (
            self.encoded_raw_files_target_dir / self.original_media_file.filename
        )

        if not raw_file_target_path.exists():
            try:
                shutil.move(
                    str(self.original_media_file.path), str(raw_file_target_path)
                )
                logger.info(
                    f"Moved original file {self.original_media_file.filename} to raw archive: {raw_file_target_path}"
                )
            except shutil.Error as e:
                logger.error(
                    f"shutil.Error moving {self.original_media_file.filename} to raw archive: {e}"
                )
            except Exception as e:
                logger.error(
                    f"Unexpected error moving {self.original_media_file.filename} to raw archive: {e}"
                )
        else:
            logger.info(
                f"Raw file {raw_file_target_path} already exists. Original {self.original_media_file.filename} not moved."
            )

    def post_actions(self):
        if self.renamed_original_file and self.renamed_original_file.exists():
            logger.debug(
                f"Post_actions: File {self.original_media_file.filename} was handled by pre-encoder (renamed to {self.renamed_original_file}), minimal post actions."
            )
            return

        if self.current_error_output_path and self.current_error_output_path.exists():
            logger.debug(
                f"Post_actions: File {self.original_media_file.filename} was moved to error path {self.current_error_output_path}, minimal post actions."
            )
            return

        if self.no_error and self.args.move_raw_file:
            self._move_original_raw_file()

        if (
            self.no_error
            and hasattr(self, "encoded_file")
            and self.encoded_file
            and self.encoded_file.exists()
        ):
            final_encoded_size = self.encoded_file.stat().st_size
            size_ratio = (
                (final_encoded_size / self.original_media_file.size * 100)
                if self.original_media_file.size > 0
                else 0
            )
            logger.success(
                f"Completed: {self.original_media_file.path.relative_to(Path.cwd())}, "
                f"total time: {format_timedelta(self.total_time if hasattr(self, 'total_time') else timedelta(0))}, "
                f"{formatted_size(self.original_media_file.size)} -> {formatted_size(final_encoded_size)} "
                f"({size_ratio:.0f}%)"
            )
        elif not self.no_error:
            logger.warning(
                f"Finished processing (with errors): {self.original_media_file.path.relative_to(Path.cwd())}"
            )
        else:
            logger.info(
                f"Finished processing (status uncertain - no_error is True but encoded file might be missing): {self.original_media_file.path.relative_to(Path.cwd())}"
            )

    def _set_metadata_comment(self):
        raise NotImplementedError("Subclasses must implement _set_metadata_comment().")


class VideoEncoder(Encoder):
    pre_encoder: PreVideoEncoder

    def __init__(self, media_file: MediaFile, args):
        super().__init__(media_file, args)

        self.encoded_dir = VIDEO_OUT_DIR_ROOT / self.original_media_file.relative_dir
        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )

        self.success_log_output_dir = self.encoded_dir

        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR / self.original_media_file.relative_dir
        )

        self.pre_encoder = PreVideoEncoder(
            media_file, self.args.manual_mode, args=self.args
        )  # Pass args

        self.video_map_cmd_part = ""
        self.audio_map_cmd_part = ""
        self.subtitle_map_cmd_part = ""

    def encode(self):
        self.encoder_codec_name = self.pre_encoder.best_encoder
        self.crf = self.pre_encoder.best_crf

        if not self.encoder_codec_name or self.crf is None or self.crf == 0:
            logger.error(
                f"Pre-encoder did not set valid best_encoder or best_crf for {self.original_media_file.filename}. Cannot encode. Encoder: '{self.encoder_codec_name}', CRF: {self.crf}"
            )
            self.no_error = False
            error_dir_for_missing_params = (
                self.error_dir_base
                / "pre_encode_param_missing"
                / self.original_media_file.relative_dir
            )
            error_dir_for_missing_params.mkdir(parents=True, exist_ok=True)
            if self.original_media_file.path.exists():
                try:
                    shutil.move(
                        str(self.original_media_file.path),
                        error_dir_for_missing_params
                        / self.original_media_file.filename,
                    )
                    self.current_error_output_path = (
                        error_dir_for_missing_params / self.original_media_file.filename
                    )
                except Exception as move_e:
                    logger.error(
                        f"Could not move file after pre-encode param missing: {move_e}"
                    )
            return

        try:
            self._ffmpeg_encode_video()
        except MP4MKVEncodeFailException as e:
            logger.error(
                f"MP4/MKV encoding failed for {self.original_media_file.filename}: {e}"
            )
            self.no_error = False
            if (
                not self.current_error_output_path
                and self.original_media_file.path.exists()
            ):
                specific_error_dir = (
                    self.error_dir_base
                    / "MP4_MKV_Encode_Failed"
                    / self.original_media_file.relative_dir
                )
                specific_error_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(
                        str(self.original_media_file.path),
                        specific_error_dir / self.original_media_file.filename,
                    )
                    self.current_error_output_path = (
                        specific_error_dir / self.original_media_file.filename
                    )
                except Exception as move_e:
                    logger.error(
                        f"Could not move file after MP4_MKV_Encode_Failed: {move_e}"
                    )
        except EncodingException as e:
            logger.error(
                f"Generic encoding error for {self.original_media_file.filename}: {e}"
            )
            self.no_error = False
            if (
                not self.current_error_output_path
                and self.original_media_file.path.exists()
            ):
                generic_ffmpeg_err_dir = (
                    self.error_dir_base
                    / "ffmpeg_generic_error"
                    / self.original_media_file.relative_dir
                )
                generic_ffmpeg_err_dir.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.move(
                        str(self.original_media_file.path),
                        generic_ffmpeg_err_dir / self.original_media_file.filename,
                    )
                    self.current_error_output_path = (
                        generic_ffmpeg_err_dir / self.original_media_file.filename
                    )
                except Exception as move_e:
                    logger.error(
                        f"Could not move file after generic ffmpeg error: {move_e}"
                    )

    def _check_and_handle_oversized(self, attempt=1, max_attempts=3) -> bool:
        if not self.encoded_file.exists():
            logger.error(
                f"_check_and_handle_oversized: Encoded file {self.encoded_file} does not exist."
            )
            self.no_error = False
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
                logger.error(
                    f"Max attempts ({max_attempts}) reached for oversized file {self.original_media_file.filename}. Cannot reduce size further with CRF."
                )
                self.no_error = False
                self.move_to_oversized_error_dir()
                return False

            crf_increment = self.crf * (MANUAL_CRF_INCREMENT_PERCENT / 100.0)
            new_crf = self.crf + max(1, int(round(crf_increment)))

            if new_crf > MAX_CRF:
                logger.error(
                    f"New CRF {new_crf} would exceed MAX_CRF {MAX_CRF}. Cannot re-encode {self.original_media_file.filename} to reduce size further."
                )
                self.no_error = False
                self.move_to_oversized_error_dir()
                return False

            self.crf = new_crf
            logger.info(
                f"Increased CRF to {self.crf} for {self.original_media_file.filename} (Attempt {attempt + 1})."
            )

            self.pre_encoder.encode_info_handler.dump(
                crf=self.crf,
                encoder=self.encoder_codec_name,
                ori_video_path=self.original_media_file.path.as_posix(),
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
                    return self._check_and_handle_oversized(
                        attempt=attempt + 1, max_attempts=max_attempts
                    )
                else:
                    logger.error(
                        f"Re-encoding attempt {attempt+1} for oversized file failed."
                    )
                    return False
            except Exception as e:
                logger.error(
                    f"Exception during oversized re-encode attempt {attempt+1}: {e}"
                )
                self.no_error = False
                if (
                    not self.current_error_output_path
                    and self.original_media_file.path.exists()
                ):
                    self.move_to_oversized_error_dir()
                return False
        else:
            logger.info(
                f"File {self.encoded_file.name} is not oversized. Size: {formatted_size(current_encoded_size)}."
            )
            if self.pre_encoder.encode_info_handler.path.exists():
                self.pre_encoder.encode_info_handler.remove_file()
            return True

    def move_to_oversized_error_dir(self):
        oversized_error_dir = (
            self.error_dir_base
            / "oversized_unfixable"
            / self.original_media_file.relative_dir
        )
        oversized_error_dir.mkdir(parents=True, exist_ok=True)
        if self.original_media_file.path.exists():
            try:
                shutil.move(
                    str(self.original_media_file.path),
                    oversized_error_dir / self.original_media_file.filename,
                )
                self.current_error_output_path = (
                    oversized_error_dir / self.original_media_file.filename
                )
                logger.info(
                    f"Moved unfixably oversized original file to {self.current_error_output_path}"
                )
            except Exception as move_e:
                logger.error(f"Could not move unfixably oversized file: {move_e}")

    def _set_metadata_comment(self, update_dic: Optional[dict] = None):
        comment_data = {
            "comment_tag": VIDEO_COMMENT_ENCODED,
            "encoder_options": {
                "codec": self.encoder_codec_name,
                "crf": self.crf,
                "target_vmaf": TARGET_VMAF,
                "pre_encode_manual_mode": self.pre_encoder.is_manual_mode
                if self.pre_encoder
                else "N/A",
            },
            "source_file_info": {
                "name": self.original_media_file.path.name,
                "size_bytes": self.original_media_file.size,
                "size_formatted": formatted_size(self.original_media_file.size),
                "md5": self.original_media_file.md5,
                "sha256": self.original_media_file.sha256,
            },
            "encoding_software_config": {"configured_encoders": VIDEO_ENCODERS_CONFIG},
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
            logger.info(
                f"Initial ffmpeg command successful for {self.encoded_file.name}."
            )
            self.no_error = True
            if self.keep_mtime:
                os.utime(
                    self.encoded_file,
                    times=(
                        datetime.now().timestamp(),
                        self.original_media_file.path.stat().st_mtime,
                    ),
                )
            if not is_oversized_retry:
                if not self._check_and_handle_oversized():
                    self.no_error = False

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
            self._build_encode_command()

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
                if self.keep_mtime:
                    os.utime(
                        self.encoded_file,
                        times=(
                            datetime.now().timestamp(),
                            self.original_media_file.path.stat().st_mtime,
                        ),
                    )
                if not self._check_and_handle_oversized():
                    self.no_error = False
            else:
                logger.error(
                    f"MKV encoding also failed for {self.original_media_file.path.name}."
                )
                if self.encoded_file.exists():
                    self.encoded_file.unlink(missing_ok=True)
                self.failed_action(
                    res_mkv.stdout if res_mkv else "",
                    res_mkv.stderr if res_mkv else "",
                    res_mkv.returncode if res_mkv else -1,
                )
                raise MP4MKVEncodeFailException(
                    f"Both MP4 and MKV encoding failed for {self.original_media_file.path.name}"
                )

        else:
            logger.error(
                f"ffmpeg command failed for {self.encoded_file.name}. Return code: {res.returncode if res else 'N/A'}"
            )
            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
            )
            raise EncodingException(
                f"ffmpeg encoding failed for {self.encoded_file.name}"
            )

    def _build_ffmpeg_stream_maps(self):
        _video_map_cmd = ""
        max_fps = 240
        if not self.pre_encoder.output_video_streams:
            logger.warning(
                f"No output video streams defined by pre-encoder for {self.original_media_file.filename}. Video encoding may fail or be empty."
            )
        for video_stream in self.pre_encoder.output_video_streams:
            fps_str = "24"
            if (
                "avg_frame_rate" in video_stream
                and video_stream["avg_frame_rate"] != "0/0"
            ):
                try:
                    fps_fraction = Fraction(str(video_stream.get("avg_frame_rate")))
                    if fps_fraction > 0 and fps_fraction <= max_fps:
                        fps_str = str(fps_fraction)
                except (ZeroDivisionError, ValueError) as e:
                    logger.error(
                        f"Error parsing avg_frame_rate '{video_stream.get('avg_frame_rate')}' for {self.original_media_file.filename}: {e}. Using default FPS."
                    )
            else:
                logger.warning(
                    f"avg_frame_rate not found or invalid in video stream for {self.original_media_file.filename}. Using default FPS."
                )

            stream_index = int(video_stream.get("index", 0))
            _video_map_cmd += f'-map 0:{stream_index} -r "{fps_str}" '
        self.video_map_cmd_part = _video_map_cmd.strip()

        _audio_map_cmd = ""
        audio_output_idx = 0
        if not self.pre_encoder.output_audio_streams:
            logger.warning(
                f"No output audio streams selected for {self.original_media_file.filename}. Output will have no audio."
            )
        for audio_stream in self.pre_encoder.output_audio_streams:
            stream_index = int(audio_stream.get("index", 0))
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
                    try:
                        original_bitrate = int(audio_stream.get("bit_rate"))
                    except ValueError:
                        pass
                elif "tags" in audio_stream and "BPS-eng" in audio_stream["tags"]:
                    try:
                        original_bitrate = int(audio_stream["tags"]["BPS-eng"])
                    except ValueError:
                        pass

                target_opus_bitrate = (
                    min(original_bitrate, max_opus_bitrate)
                    if original_bitrate > 0
                    else max_opus_bitrate // 2
                )
                abitrate_cmd = f"-b:a:{audio_output_idx} {target_opus_bitrate} "

                if not is_mkv_target:
                    logger.info(
                        f"Audio stream {stream_index} will be Opus; ensuring output is MKV for compatibility by changing self.encoded_file."
                    )
                    self.encoded_file = self.encoded_file.with_suffix(".mkv")

            _audio_map_cmd += f"-map 0:{stream_index} -c:a:{audio_output_idx} {acodec_name} {abitrate_cmd}"
            audio_output_idx += 1
        self.audio_map_cmd_part = _audio_map_cmd.strip()

        _subtitle_map_cmd = ""
        subtitle_output_idx = 0
        if not self.pre_encoder.output_subtitle_streams:
            logger.debug(
                f"No output subtitle streams for {self.original_media_file.filename}."
            )
        for subtitle_stream in self.pre_encoder.output_subtitle_streams:
            stream_index = int(subtitle_stream.get("index", 0))
            scodec_name = "mov_text"

            input_subtitle_codec = subtitle_stream.get("codec_name", "").lower()
            if self.encoded_file.suffix.lower() == ".mkv":
                if input_subtitle_codec in SUBTITLE_MKV_CODECS:
                    scodec_name = "copy"
            elif (
                self.encoded_file.suffix.lower() != ".mkv"
                and input_subtitle_codec not in ["mov_text", "tx3g"]
            ):
                logger.warning(
                    f"Subtitle stream {stream_index} (codec {input_subtitle_codec}) might not be ideal for MP4. Trying mov_text conversion."
                )

            _subtitle_map_cmd += (
                f"-map 0:{stream_index} -c:s:{subtitle_output_idx} {scodec_name} "
            )
            subtitle_output_idx += 1
        self.subtitle_map_cmd_part = _subtitle_map_cmd.strip()

    def _build_encode_command(self):
        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path}" '
            f'-c:v "{self.encoder_codec_name}" -crf {self.crf} '
            f"{self.video_map_cmd_part} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f"{self.audio_map_cmd_part} {self.subtitle_map_cmd_part} "  # audio_map_cmd_part will be empty if no audio
            f'"{self.encoded_file}"'
        ).strip()
        logger.debug(
            f"Built ffmpeg command for {self.original_media_file.filename}:\n{self.encode_cmd_str}"
        )

    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        video_specific_log_data = {
            "pre_encode_info": {
                "estimated_size_ratio_percent": (
                    float(self.pre_encoder.best_ratio * 100)
                    if self.pre_encoder.best_ratio is not None
                    else None
                ),
                "crf_checking_time_formatted": format_timedelta(
                    self.pre_encoder.crf_checking_time
                )
                if self.pre_encoder.crf_checking_time
                else "0s",
                "target_vmaf_for_pre_encode": TARGET_VMAF,
            },
            "final_encoder_codec": self.encoder_codec_name,
            "final_crf": self.crf,
        }
        if update_dic:
            video_specific_log_data.update(update_dic)

        super().write_success_log(
            log_date_in_filename=log_date_in_filename,
            update_dic=video_specific_log_data,
        )


class PhoneVideoEncoder(Encoder):
    def __init__(self, media_file: MediaFile, args):
        super().__init__(media_file, args)
        self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()
        self.encoder_codec_name = VIDEO_CODEC_IPHONE_XR
        self.cmd_options_phone = IPHONE_XR_OPTIONS

        self.success_log_output_dir = Path.cwd()
        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR
            / "phone_encoded_raw"
            / self.original_media_file.relative_dir
        )

    def _set_metadata_comment(self):
        comment_data = {
            "comment_tag": VIDEO_COMMENT_ENCODED,
            "encoding_profile": "iPhone_XR_Optimized",
            "target_codec_video": VIDEO_CODEC_IPHONE_XR,
            "target_codec_audio": AUDIO_CODEC_IPHONE_XR,
            "source_file_info": {
                "name": self.original_media_file.filename,
                "size_formatted": formatted_size(self.original_media_file.size),
            },
        }
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

    def encode(self):
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        video_bitrate_str = f"{MANUAL_VIDEO_BIT_RATE_IPHONE_XR}"
        audio_bitrate_str = f"{MANUAL_AUDIO_BIT_RATE_IPHONE_XR}"

        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path.as_posix()}" '
            f"{self.cmd_options_phone} "
            f"-c:v {self.encoder_codec_name} -c:a {AUDIO_CODEC_IPHONE_XR} "
            f"-b:v {video_bitrate_str} -b:a {audio_bitrate_str} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f'"{self.encoded_file}"'
        ).strip()
        logger.debug(f"PhoneVideoEncoder command: {self.encode_cmd_str}")

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
            if self.keep_mtime:
                os.utime(
                    self.encoded_file,
                    (
                        datetime.now().timestamp(),
                        self.original_media_file.path.stat().st_mtime,
                    ),
                )
            self.encoded_size = self.encoded_file.stat().st_size
        else:
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
            )
            raise EncodingException(
                f"PhoneVideoEncoding failed for {self.original_media_file.filename}"
            )

    def write_success_log(
        self, log_date_in_filename=False, update_dic: Optional[dict] = None
    ):
        super().write_success_log(
            log_date_in_filename=log_date_in_filename, update_dic=update_dic
        )

    def post_actions(self):
        super().post_actions()
        if self.no_error and hasattr(self, "encode_time"):
            logger.success(
                f"Phone encode completed: {os.path.relpath(self.original_media_file.path)} "
                f"({format_timedelta(self.encode_time)})"
            )


class AudioEncoder(Encoder):
    def __init__(
        self,
        media_file: MediaFile,
        target_bit_rate: int = TARGET_BIT_RATE_IPHONE_XR,
        args=None,
    ):
        super().__init__(media_file=media_file, args=args)
        self.encoder_codec_name = DEFAULT_AUDIO_ENCODER
        self.target_bit_rate = target_bit_rate

        self.encoded_dir = (
            AUDIO_ENCODED_ROOT_DIR / self.original_media_file.relative_dir
        )
        self.encoded_file = (
            self.encoded_dir
            / self.original_media_file.path.with_suffix(self._get_file_extension()).name
        )

        self.success_log_output_dir = self.encoded_dir

        self.encoded_raw_files_target_dir = (
            AUDIO_ENCODED_RAW_DIR / self.original_media_file.relative_dir
        )

    def _get_file_extension(self) -> str:
        if self.encoder_codec_name == "libopus":
            return ".opus"
        elif self.encoder_codec_name == "libmp3lame":
            return ".mp3"
        else:
            logger.warning(
                f"Unknown audio encoder '{self.encoder_codec_name}' for extension. Defaulting to .bin"
            )
            return ".bin"

    def _set_metadata_comment(self):
        comment_data = {
            "comment_tag": AUDIO_COMMENT_ENCODED,
            "encoder_profile": "AudioOptimized",
            "target_codec": self.encoder_codec_name,
            "target_bitrate_bps": self.target_bit_rate,
            "source_file_info": {
                "name": self.original_media_file.filename,
                "size_formatted": formatted_size(self.original_media_file.size),
            },
        }
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

    def encode(self):
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        self.encode_cmd_str = (
            f'ffmpeg -y -i "{self.original_media_file.path}" '
            f"-vn "
            f"-c:a {self.encoder_codec_name} "
            f"-b:a {self.target_bit_rate} "
            f'-metadata comment="{self.encoded_comment_text}" '
            f'"{self.encoded_file}"'
        ).strip()
        logger.debug(f"AudioEncoder command: {self.encode_cmd_str}")

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
            if self.keep_mtime:
                os.utime(self.encoded_file, (datetime.now().timestamp(), self.original_media_file.path.stat().st_mtime))
            self.encoded_size = self.encoded_file.stat().st_size
        else:
            self.failed_action(res.stdout if res else "", res.stderr if res else "", res.returncode if res else -1)
            raise EncodingException(f"AudioEncoding failed for {self.original_media_file.filename}")