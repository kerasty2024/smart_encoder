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

# Utils
from ..utils.ffmpeg_utils import (
    run_cmd,
    detect_audio_language_multi_segments,
)
from ..utils.format_utils import format_timedelta

# Domain
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

# Config
from ..config.common import (
    BASE_ERROR_DIR, LANGUAGE_WORDS, MAX_ENCODE_RETRIES,
    JOB_STATUS_PENDING, JOB_STATUS_PREPROCESSING_STARTED, JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE,
    JOB_STATUS_ERROR_RETRYABLE, JOB_STATUS_ERROR_PERMANENT, JOB_STATUS_SKIPPED
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
    media_file: MediaFile
    start_time: datetime
    output_base_dir: Path
    error_dir_base: Path = BASE_ERROR_DIR.resolve()
    renamed_file_on_skip_or_error: Optional[Path] = None
    bit_rate_low_threshold: int
    is_manual_mode: bool
    args: Any
    encode_info_handler: EncodeInfo
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
        self.media_file = media_file
        self.is_manual_mode = manual_mode_flag
        self.args = args
        self.start_time = datetime.now()

        self.comment_tag_for_encoded_check = comment_tag_encoded
        self.bit_rate_relevant = relevant_bitrate_for_check
        self.encode_stream_count_relevant = relevant_stream_count_for_check
        self.bit_rate_low_threshold = low_bitrate_threshold_config
        self.output_base_dir = output_base_dir_config.resolve()

        self.file_specific_output_dir = (
            self.output_base_dir / self.media_file.relative_dir
        )
        self.file_specific_output_dir.mkdir(parents=True, exist_ok=True)

        self.skip_log_path = self.file_specific_output_dir / "pre_encode_skipped.txt"
        encode_info_storage_dir = self.file_specific_output_dir / ".encode_info_cache"
        self.encode_info_handler = EncodeInfo(
            self.media_file.md5, storage_dir=encode_info_storage_dir
        )

        if not self.encode_info_handler.load():
            self.encode_info_handler.dump(status=JOB_STATUS_PENDING,
                                          ori_video_path=str(self.media_file.path),
                                          pre_encoder_data={"is_manual_mode": self.is_manual_mode})
        else:
            if self.encode_info_handler.pre_encoder_data:
                loaded_manual_mode = self.encode_info_handler.pre_encoder_data.get("is_manual_mode")
                if loaded_manual_mode is not None:
                    self.is_manual_mode = loaded_manual_mode
                if self.encode_info_handler.status in [JOB_STATUS_CRF_SEARCH_STARTED, JOB_STATUS_PREPROCESSING_DONE]:
                    self.output_video_streams = self.encode_info_handler.pre_encoder_data.get("output_video_streams", [])
                    self.output_audio_streams = self.encode_info_handler.pre_encoder_data.get("output_audio_streams", [])
                    self.output_subtitle_streams = self.encode_info_handler.pre_encoder_data.get("output_subtitle_streams", [])
                    self.best_encoder = self.encode_info_handler.encoder or ""
                    self.best_crf = self.encode_info_handler.crf or 0
                    self.best_ratio = self.encode_info_handler.pre_encoder_data.get("best_ratio")
                    time_sec = self.encode_info_handler.pre_encoder_data.get("crf_checking_time_seconds")
                    if time_sec is not None:
                        self.crf_checking_time = timedelta(seconds=time_sec)


    def start(self):
        logger.debug(f"PreEncoder start for: {self.media_file.filename} (Status: {self.encode_info_handler.status}, Manual: {self.is_manual_mode})")

        if self.encode_info_handler.status == JOB_STATUS_PREPROCESSING_DONE:
            logger.info(f"Preprocessing already completed for {self.media_file.filename}. Loading results into PreEncoder instance.")
            if self.encode_info_handler.pre_encoder_data:
                self.best_encoder = self.encode_info_handler.encoder or self.encode_info_handler.pre_encoder_data.get("best_encoder", "")
                self.best_crf = self.encode_info_handler.crf if self.encode_info_handler.crf is not None else self.encode_info_handler.pre_encoder_data.get("best_crf", 0)
                self.output_video_streams = self.encode_info_handler.pre_encoder_data.get("output_video_streams", [])
                self.output_audio_streams = self.encode_info_handler.pre_encoder_data.get("output_audio_streams", [])
                self.output_subtitle_streams = self.encode_info_handler.pre_encoder_data.get("output_subtitle_streams", [])
                self.is_manual_mode = self.encode_info_handler.pre_encoder_data.get("is_manual_mode", self.is_manual_mode)
                self.best_ratio = self.encode_info_handler.pre_encoder_data.get("best_ratio")
                time_sec = self.encode_info_handler.pre_encoder_data.get("crf_checking_time_seconds")
                if time_sec is not None:
                    self.crf_checking_time = timedelta(seconds=time_sec)
                else:
                    self.crf_checking_time = timedelta(0) if self.is_manual_mode else None
            else:
                logger.warning(f"Status for {self.media_file.filename} is PREPROCESSING_DONE, but pre_encoder_data is missing. Proceeding cautiously.")
            return

        try:
            if self.encode_info_handler.status not in [JOB_STATUS_CRF_SEARCH_STARTED]:
                current_pre_data = self.encode_info_handler.pre_encoder_data or {}
                current_pre_data["is_manual_mode"] = self.is_manual_mode
                self.encode_info_handler.dump(status=JOB_STATUS_PREPROCESSING_STARTED,
                                              pre_encoder_data=current_pre_data)

            self._check_if_file_should_be_skipped()
            self._determine_optimal_encoding_options()

            pre_encoder_results = {
                "best_encoder": self.best_encoder,
                "best_crf": self.best_crf,
                "output_video_streams": self.output_video_streams,
                "output_audio_streams": self.output_audio_streams,
                "output_subtitle_streams": self.output_subtitle_streams,
                "crf_checking_time_seconds": self.crf_checking_time.total_seconds() if self.crf_checking_time else None,
                "best_ratio": self.best_ratio,
                "is_manual_mode": self.is_manual_mode,
            }
            self.encode_info_handler.dump(status=JOB_STATUS_PREPROCESSING_DONE,
                                          encoder=self.best_encoder,
                                          crf=self.best_crf,
                                          pre_encoder_data=pre_encoder_results)

        except SkippedFileException as e:
            self.encode_info_handler.dump(status=JOB_STATUS_SKIPPED, last_error_message=str(e))
            raise
        except PreprocessingException as e:
            logger.error(f"Controlled Preprocessing error for {self.media_file.filename}: {e}")
            is_permanent = isinstance(e, NoAudioStreamException) and not (self.args and getattr(self.args, "allow_no_audio", False))
            current_status_on_error = JOB_STATUS_ERROR_PERMANENT if is_permanent else JOB_STATUS_ERROR_RETRYABLE
            increment_retry_on_error = not is_permanent

            self.encode_info_handler.dump(status=current_status_on_error,
                                          last_error_message=str(e),
                                          increment_retry_count=increment_retry_on_error)

            if not self.renamed_file_on_skip_or_error:
                 if current_status_on_error == JOB_STATUS_ERROR_PERMANENT or \
                    (increment_retry_on_error and self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES):
                    error_type_name = type(e).__name__
                    if increment_retry_on_error and self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES:
                        error_type_name = f"max_retries_{error_type_name}"
                        self.encode_info_handler.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=f"Max retries: {str(e)}")
                    self.move_file_to_error_dir(error_subdir_name=f"preproc_err_{error_type_name}")
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error during PreEncoder.start for {self.media_file.filename}: {e}",
                exc_info=True,
            )
            self.encode_info_handler.dump(status=JOB_STATUS_ERROR_RETRYABLE,
                                          last_error_message=f"Unexpected PreEncoder: {str(e)}",
                                          increment_retry_count=True)
            if not self.renamed_file_on_skip_or_error:
                if self.encode_info_handler.retry_count >= MAX_ENCODE_RETRIES:
                    self.encode_info_handler.dump(status=JOB_STATUS_ERROR_PERMANENT, last_error_message=f"Max retries, Unexpected PreEncoder: {str(e)}")
                    self.move_file_to_error_dir(error_subdir_name="preproc_unexpected_err_max_retries")
            raise PreprocessingException(f"Unexpected pre-encoder failure: {e}") from e


    def _check_if_file_should_be_skipped(self):
        skip_reason = None
        exception_type: Optional[Type[PreprocessingException]] = None
        log_level_for_skip = logger.info

        if (
            self.comment_tag_for_encoded_check
            and self.comment_tag_for_encoded_check in self.media_file.comment
        ):
            skip_reason = f"Already encoded (comment tag '{self.comment_tag_for_encoded_check}' found)"
            exception_type = FileAlreadyEncodedException
        elif self.bit_rate_relevant <= self.bit_rate_low_threshold:
            skip_reason = f"Bitrate ({self.bit_rate_relevant}bps) is at or below threshold ({self.bit_rate_low_threshold}bps)"
            exception_type = BitRateTooLowException
        elif (
            hasattr(self.media_file, "vcodec")
            and self.media_file.vcodec in EXCEPT_VIDEO_FORMATS
        ):
            skip_reason = f"Format '{self.media_file.vcodec}' is in excluded list"
            exception_type = FormatExcludedException
            log_level_for_skip = logger.debug
        elif self.encode_stream_count_relevant == 0:
            skip_reason = "No relevant streams to process"
            exception_type = NoStreamsFoundException
            log_level_for_skip = logger.debug


        if skip_reason and exception_type:
            log_level_for_skip(f"Skipping {self.media_file.filename}: {skip_reason}")
            with self.skip_log_path.open("a", encoding="utf-8") as log_f:
                log_f.write(
                    f"{datetime.now()}: {self.media_file.path} - {skip_reason}\n"
                )

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
                    else:
                        logger.debug(f"Skipped file {self.media_file.filename} is already at skip target path {target_skip_path}.")
                        self.renamed_file_on_skip_or_error = target_skip_path
                except Exception as move_err:
                    logger.error(
                        f"Could not move skipped file {self.media_file.filename} to {target_skip_path}: {move_err}"
                    )
            else:
                self.renamed_file_on_skip_or_error = target_skip_path
            raise exception_type(skip_reason)

    def _determine_optimal_encoding_options(self):
        raise NotImplementedError(
            "Subclasses must implement _determine_optimal_encoding_options()."
        )

    def move_file_to_error_dir(self, error_subdir_name: str):
        if (
            self.renamed_file_on_skip_or_error
            and self.renamed_file_on_skip_or_error.exists()
            and self.renamed_file_on_skip_or_error.resolve() != self.media_file.path.resolve()
        ):
            logger.debug(
                f"File {self.media_file.filename} already moved/renamed to {self.renamed_file_on_skip_or_error}. Skipping move_file_to_error_dir to {error_subdir_name}."
            )
            return

        target_error_dir = (
            self.error_dir_base / error_subdir_name / self.media_file.relative_dir
        )
        target_error_dir.mkdir(parents=True, exist_ok=True)
        target_error_path = target_error_dir / self.media_file.filename

        if self.media_file.path.exists():
            if self.media_file.path.resolve() != target_error_path.resolve():
                try:
                    shutil.move(str(self.media_file.path), str(target_error_path))
                    self.renamed_file_on_skip_or_error = target_error_path
                    logger.info(
                        f"Moved file {self.media_file.filename} to error directory: {target_error_path}"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to move file {self.media_file.filename} to error dir {target_error_path}: {e}"
                    )
            else:
                logger.debug(f"File {self.media_file.filename} is already at error target path {target_error_path}.")
                self.renamed_file_on_skip_or_error = target_error_path
        else:
            logger.warning(
                f"Original file {self.media_file.path} not found, cannot move to error dir {target_error_path}."
            )
            self.renamed_file_on_skip_or_error = target_error_path


class PreVideoEncoder(PreEncoder):
    def __init__(
        self,
        media_file: MediaFile,
        manual_mode_flag: bool = False,
        args: Optional[Any] = None,
    ):
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
        if not (self.output_video_streams or self.output_audio_streams or self.output_subtitle_streams):
            logger.debug(f"No stream selections loaded for {self.media_file.filename}, performing selection now.")
            self._select_output_video_streams()
            try:
                self._select_output_audio_streams()
            except NoAudioStreamException as e:
                if self.args and getattr(self.args, "allow_no_audio", False):
                    logger.warning(
                        f"No suitable audio stream for {self.media_file.filename}: {e}. Encoding will proceed without audio as per --allow-no-audio."
                    )
                    self.output_audio_streams = []
                else:
                    raise
            self._select_output_subtitle_streams()
            current_pre_data = self.encode_info_handler.pre_encoder_data or {}
            current_pre_data.update({
                "output_video_streams": self.output_video_streams,
                "output_audio_streams": self.output_audio_streams,
                "output_subtitle_streams": self.output_subtitle_streams,
                "is_manual_mode": self.is_manual_mode,
            })
            self.encode_info_handler.dump(pre_encoder_data=current_pre_data)
        else:
            logger.debug(f"Stream selections already populated for {self.media_file.filename} (likely from resume).")

        if self.is_manual_mode:
            self.best_crf = MANUAL_CRF
            self.best_encoder = (
                self.available_encoders_cfg[0]
                if self.available_encoders_cfg
                else AV1_ENCODER
            )
            self.crf_checking_time = timedelta(0)
            self.best_ratio = None
            logger.info(
                f"Manual mode for {self.media_file.filename}: Using Encoder {self.best_encoder}, CRF {self.best_crf}."
            )
            return

        if self.encode_info_handler.status not in [JOB_STATUS_CRF_SEARCH_STARTED, JOB_STATUS_PREPROCESSING_DONE]:
            self.encode_info_handler.dump(status=JOB_STATUS_CRF_SEARCH_STARTED,
                                          encoder="", crf=0,
                                          pre_encoder_data=self.encode_info_handler.pre_encoder_data)

        crf_search_start_time = datetime.now()
        current_best_ratio_found: Optional[float] = self.best_ratio
        start_encoder_index = 0
        if self.encode_info_handler.status == JOB_STATUS_CRF_SEARCH_STARTED and self.encode_info_handler.encoder:
            try:
                encoder_in_progress = self.encode_info_handler.encoder
                start_encoder_index = self.available_encoders_cfg.index(encoder_in_progress)
                if self.encode_info_handler.crf != 0:
                    logger.debug(f"CRF search for '{encoder_in_progress}' was previously completed (CRF: {self.encode_info_handler.crf}). Resuming with next encoder.")
                    start_encoder_index += 1
                else:
                    logger.debug(f"Resuming CRF search, starting/retrying with encoder: '{encoder_in_progress}'.")
            except ValueError:
                logger.warning(f"Encoder '{self.encode_info_handler.encoder}' from progress not in current config {self.available_encoders_cfg}. Restarting CRF search.")
                start_encoder_index = 0
                current_best_ratio_found = None
                self.best_encoder = ""
                self.best_crf = 0
                self.best_ratio = None
        elif self.encode_info_handler.status != JOB_STATUS_CRF_SEARCH_STARTED :
             current_best_ratio_found = None
             self.best_encoder = ""
             self.best_crf = 0
             self.best_ratio = None

        all_searches_failed_flag = True
        for i in range(start_encoder_index, len(self.available_encoders_cfg)):
            encoder_candidate = self.available_encoders_cfg[i]
            logger.debug(f"Starting/Resuming CRF search for encoder: {encoder_candidate} on {self.media_file.filename}")
            try:
                self.encode_info_handler.dump(encoder=encoder_candidate, crf=0,
                                              pre_encoder_data=self.encode_info_handler.pre_encoder_data)
                crf_val, encoded_ratio_val = self._perform_crf_search_for_encoder(
                    encoder_candidate
                )
                encoded_ratio_float = encoded_ratio_val / 100.0
                logger.debug(f"CRF search for {encoder_candidate} successful: CRF {crf_val}, Ratio {encoded_ratio_float:.2%}")
                all_searches_failed_flag = False

                if (
                    current_best_ratio_found is None
                    or encoded_ratio_float < current_best_ratio_found
                ):
                    current_best_ratio_found = encoded_ratio_float
                    self.best_encoder = encoder_candidate
                    self.best_crf = crf_val
                    self.best_ratio = encoded_ratio_float
                    self.encode_info_handler.dump(encoder=self.best_encoder, crf=self.best_crf,
                                                  pre_encoder_data=self.encode_info_handler.pre_encoder_data)
            except CRFSearchFailedException as e:
                logger.warning(
                    f"CRF search failed for encoder {encoder_candidate} on {self.media_file.filename}: {e}. Trying next encoder if available."
                )
                if self.encode_info_handler.encoder == encoder_candidate:
                     self.encode_info_handler.dump(crf=0, pre_encoder_data=self.encode_info_handler.pre_encoder_data)
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error during CRF search for {encoder_candidate} on {self.media_file.filename}: {e}",
                    exc_info=True,
                )
                if self.encode_info_handler.encoder == encoder_candidate:
                    self.encode_info_handler.dump(crf=0, pre_encoder_data=self.encode_info_handler.pre_encoder_data)
                continue

        self.crf_checking_time = datetime.now() - crf_search_start_time

        if all_searches_failed_flag or not self.best_encoder or self.best_crf == 0:
            logger.warning(
                f"CRF search failed for all configured encoders for {self.media_file.filename}. "
                f"Falling back to manual CRF mode (Encoder: {AVAILABLE_ENCODERS[0] if AVAILABLE_ENCODERS else AV1_ENCODER}, CRF: {MANUAL_CRF})."
            )
            self.is_manual_mode = True
            self.best_crf = MANUAL_CRF
            self.best_encoder = AVAILABLE_ENCODERS[0] if AVAILABLE_ENCODERS else AV1_ENCODER
            self.best_ratio = None
            self.crf_checking_time = timedelta(0)
        else:
            ratio_log_str = (
                f"{self.best_ratio:.2%}" if self.best_ratio is not None else "N/A"
            )
            logger.info(
                f"Optimal pre-encode params determined for {self.media_file.filename}: Encoder {self.best_encoder}, CRF {self.best_crf}, Ratio {ratio_log_str}. Time: {format_timedelta(self.crf_checking_time)}"
            )

    def _perform_crf_search_for_encoder(self, encoder_to_test: str) -> Tuple[int, int]:
        if (
            self.renamed_file_on_skip_or_error
            and self.renamed_file_on_skip_or_error.exists()
            and self.renamed_file_on_skip_or_error.resolve() != self.media_file.path.resolve()
        ):
            logger.info(
                f"CRF search: File {self.media_file.filename} appears to have been moved/renamed to {self.renamed_file_on_skip_or_error}. Skipping CRF search for {encoder_to_test}."
            )
            raise SkippedFileException(
                f"File {self.media_file.filename} no longer at original path for CRF search."
            )

        temp_dir_for_ab_av1: Optional[str] = None
        if (
            self.args
            and hasattr(self.args, "temp_work_dir")
            and self.args.temp_work_dir
        ):
            temp_dir_for_ab_av1 = str(Path(self.args.temp_work_dir).resolve())
            logger.debug(
                f"Using specified temporary directory for ab-av1: {temp_dir_for_ab_av1}"
            )

        cmd_list = [
            "ab-av1", "crf-search",
            "-e", encoder_to_test,
            "-i", str(self.media_file.path.resolve()),
            "--sample-every", SAMPLE_EVERY,
            "--max-encoded-percent", str(MAX_ENCODED_PERCENT),
            "--min-vmaf", str(TARGET_VMAF)
        ]
        if temp_dir_for_ab_av1:
            cmd_list.extend(["--temp-dir", temp_dir_for_ab_av1])

        try:
            display_cmd = subprocess.list2cmdline(cmd_list) if os.name == 'nt' else " ".join(shlex.quote(s) for s in cmd_list)
        except AttributeError:
            display_cmd = " ".join(shlex.quote(s) for s in cmd_list)
        logger.debug(
            f"Executing CRF search for {self.media_file.filename} with {encoder_to_test}: {display_cmd}"
        )

        res = run_cmd(
            cmd_list,
            src_file_for_log=self.media_file.path,
            show_cmd=__debug__,
        )

        if res is None or res.returncode != 0:
            err_msg = f"ab-av1 crf-search command failed for {encoder_to_test}. Return code: {res.returncode if res else 'N/A'}."
            if res and res.stderr:
                err_msg += f" Stderr: {res.stderr}"
            logger.debug(err_msg)

            crf_check_error_dir = (
                self.error_dir_base / "crf_check_errors" / self.media_file.relative_dir
            )
            if not crf_check_error_dir.exists():
                 crf_check_error_dir.mkdir(parents=True, exist_ok=True)

            debug_ab_av1_output_path = (
                crf_check_error_dir / f"{self.media_file.filename}.{encoder_to_test}.ab_av1_output.txt"
            )
            with debug_ab_av1_output_path.open("w", encoding="utf-8") as f_debug:
                f_debug.write(
                    f"Command: {display_cmd}\n\nStdout:\n{res.stdout if res else 'N/A'}\n\nStderr:\n{res.stderr if res else 'N/A'}"
                )
            logger.debug(f"ab-av1 CRF search debug output logged to: {debug_ab_av1_output_path}")
            raise CRFSearchFailedException(err_msg)

        stdout_lower = res.stdout.lower()
        crf_match = re.search(r"crf\s+(\d+)", stdout_lower)
        ratio_match_paren = re.search(r"\((\d+)%\)", stdout_lower)
        ratio_match_direct = re.search(r"ratio\s+(\d+)%", stdout_lower)

        encoded_ratio_percent = None
        if ratio_match_paren:
            encoded_ratio_percent = int(ratio_match_paren.group(1))
        elif ratio_match_direct:
            encoded_ratio_percent = int(ratio_match_direct.group(1))

        if crf_match and encoded_ratio_percent is not None:
            crf = int(crf_match.group(1))
            logger.debug(
                f"CRF search for {encoder_to_test} on {self.media_file.filename} resulted in: CRF {crf}, Ratio {encoded_ratio_percent}%"
            )
            if (
                crf <= 0
                or encoded_ratio_percent <= 0
                or encoded_ratio_percent > MAX_ENCODED_PERCENT + 15 # Allow some margin for ab-av1's max-encoded-percent behavior
            ):
                raise CRFSearchFailedException(
                    f"CRF search for {encoder_to_test} yielded potentially invalid results: CRF {crf}, Ratio {encoded_ratio_percent}%"
                )
            return crf, encoded_ratio_percent
        else:
            err_msg = f"Could not parse CRF and/or Ratio from ab-av1 output for {encoder_to_test}. Output: {res.stdout}"
            logger.error(err_msg)
            crf_check_error_dir = (
                self.error_dir_base / "crf_check_errors" / self.media_file.relative_dir
            )
            if not crf_check_error_dir.exists():
                 crf_check_error_dir.mkdir(parents=True, exist_ok=True)
            debug_ab_av1_output_path = (
                crf_check_error_dir / f"{self.media_file.filename}.{encoder_to_test}.ab_av1_output.txt"
            )
            with debug_ab_av1_output_path.open("w", encoding="utf-8") as f_debug:
                f_debug.write(
                    f"Command: {display_cmd}\n\nStdout:\n{res.stdout}\n\nStderr:\n{res.stderr if res else 'N/A'}"
                )
            logger.debug(f"ab-av1 CRF search debug output (parse error) logged to: {debug_ab_av1_output_path}")
            raise CRFSearchFailedException(err_msg)

    def _select_output_video_streams(self):
        if not self.media_file.video_streams:
            logger.warning(
                f"No video streams found in {self.media_file.filename} by MediaFile analysis."
            )
            self.output_video_streams = []
            return

        # 異常なフレームレートを持つビデオストリームをフィルタリングする可能性を考慮
        # 例: 1000 FPSを超えるものは異常とみなすなど
        # ただし、現状のMediaInfoでは 1499 FPS となっているので、このフィルタが有効かは不明
        # ひとまずは現状のロジックを維持し、MediaFile側でより正確なフレームレートが取得できる前提とする
        valid_video_streams = []
        for stream in self.media_file.video_streams:
            is_valid_fps = False
            avg_fps_str = stream.get("avg_frame_rate", "0/0")
            if avg_fps_str != "0/0":
                try:
                    num, den = map(int, avg_fps_str.split('/'))
                    if den != 0:
                        fps_val = num / den
                        # 一般的なビデオフレームレートの範囲に収まっているかチェック (例: 1 FPS以上, 121 FPS未満)
                        # 今回の 1499 FPS はこの範囲外になる
                        if 1 <= fps_val < 121: # この上限は調整可能
                            is_valid_fps = True
                        else:
                            logger.warning(f"Video stream index {stream.get('index')} for {self.media_file.filename} has unusual frame rate: {fps_val} FPS. Will be excluded if other suitable streams exist.")
                except ValueError:
                    logger.warning(f"Could not parse avg_frame_rate '{avg_fps_str}' for video stream index {stream.get('index')} in {self.media_file.filename}.")

            codec_name = stream.get("codec_name", "").lower()
            if is_valid_fps and codec_name not in SKIP_VIDEO_CODEC_NAMES:
                valid_video_streams.append(stream)
            elif not is_valid_fps and codec_name not in SKIP_VIDEO_CODEC_NAMES :
                # FPSが異常でも、他にストリームがない場合は選択肢として残すかもしれないが、
                # 通常はエンコードエラーの原因になるため、除外する方が安全
                logger.debug(f"Skipping video stream index {stream.get('index')} for {self.media_file.filename} due to invalid/unusual FPS or excluded codec ({codec_name}).")


        if not valid_video_streams and self.media_file.video_streams:
            # 有効なFPSのストリームが一つもなかったが、元々はビデオストリームがあった場合
            # 最も可能性の高そうなものを一つ選ぶか、エラーとするか
            # ここでは、元々あったビデオストリームから最初のものを選択する（ただし警告を出す）
            logger.warning(f"No video streams with typical FPS found for {self.media_file.filename}. "
                           f"Attempting to use the first video stream found (index {self.media_file.video_streams[0].get('index')}), "
                           f"but it may have issues (e.g., reported FPS: {self.media_file.video_streams[0].get('avg_frame_rate')}).")
            first_stream = self.media_file.video_streams[0]
            if first_stream.get("codec_name","").lower() not in SKIP_VIDEO_CODEC_NAMES:
                 self.output_video_streams = [first_stream]
            else:
                 self.output_video_streams = []
        elif valid_video_streams:
            # 複数の有効なビデオストリームがある場合、最初のものを選択 (または他の基準で選択)
            self.output_video_streams = [valid_video_streams[0]]
            if len(valid_video_streams) > 1:
                logger.info(f"Multiple suitable video streams found for {self.media_file.filename}. Selected stream index {valid_video_streams[0].get('index')}.")
        else:
            self.output_video_streams = []


        if not self.output_video_streams:
             logger.warning(f"No suitable video streams selected for {self.media_file.filename}.")


    def _select_output_audio_streams(self):
        if not self.media_file.audio_streams:
            # --allow-no-audio フラグが立っていなければ NoAudioStreamException を発生させる
            if not (self.args and getattr(self.args, "allow_no_audio", False)):
                raise NoAudioStreamException(
                    f"No audio streams at all in {self.media_file.filename}."
                )
            else: # allow_no_audio が True の場合は、オーディオストリームなしで処理を続行
                logger.warning(f"No audio streams found in {self.media_file.filename}, but --allow-no-audio is set. Proceeding without audio.")
                self.output_audio_streams = []
                return

        # オーディオストリームが1つしかない場合は、言語検出をスキップし、それを選択する
        if len(self.media_file.audio_streams) == 1:
            stream = self.media_file.audio_streams[0]
            # 最低限のチェック（サンプルレートなど）は行っても良い
            sample_rate_str = stream.get("sample_rate")
            is_valid_sample_rate = False
            if sample_rate_str:
                try:
                    sample_rate = int(float(sample_rate_str))
                    if sample_rate >= 8000: # 一般的な最小サンプルレート
                        is_valid_sample_rate = True
                except ValueError:
                    pass

            if is_valid_sample_rate:
                logger.debug(
                    f"Only one audio stream found in {self.media_file.filename} (index {stream.get('index')}). "
                    f"Skipping language detection and selecting this stream by default."
                )
                self.output_audio_streams = [stream]
                return
            else: # サンプルレートが無効など、明らかに不適切なストリームの場合
                if not (self.args and getattr(self.args, "allow_no_audio", False)):
                    raise NoAudioStreamException(
                        f"Single audio stream in {self.media_file.filename} is unsuitable (e.g., invalid sample rate: {sample_rate_str})."
                    )
                else:
                    logger.warning(f"Single audio stream in {self.media_file.filename} is unsuitable (e.g., invalid sample rate: {sample_rate_str}), "
                                   f"but --allow-no-audio is set. Proceeding without audio.")
                    self.output_audio_streams = []
                    return


        # 複数のオーディオストリームがある場合は、従来通り言語検出を行う
        suitable_audio_streams = []
        for stream in self.media_file.audio_streams:
            if "sample_rate" in stream:
                try:
                    sample_rate = int(float(stream.get("sample_rate", 0)))
                    if sample_rate < 1000: # より現実的な下限値 (例: 8kHz未満はスキップ)
                        logger.debug(
                            f"Skipping audio stream index {stream.get('index')} for {self.media_file.filename}: low sample rate {sample_rate}."
                        )
                        continue
                except ValueError:
                    logger.debug(
                        f"Skipping audio stream index {stream.get('index')} for {self.media_file.filename}: invalid sample rate format '{stream.get('sample_rate')}'."
                    )
                    continue
            if self._is_audio_stream_language_suitable(stream):
                suitable_audio_streams.append(stream)

        if not suitable_audio_streams:
            if not (self.args and getattr(self.args, "allow_no_audio", False)):
                raise NoAudioStreamException(
                    f"No audio streams match desired languages after filtering for {self.media_file.filename}."
                )
            else:
                logger.warning(f"No suitable audio streams found for {self.media_file.filename} after language detection, "
                               f"but --allow-no-audio is set. Proceeding without audio.")
                self.output_audio_streams = []
                return

        self.output_audio_streams = suitable_audio_streams
        logger.debug(
            f"Selected {len(self.output_audio_streams)} audio streams for {self.media_file.filename} after language detection."
        )

    def _is_audio_stream_language_suitable(self, stream_data: Dict) -> bool:
        lang_tag = stream_data.get("tags", {}).get("language", "").lower().strip()

        if lang_tag and lang_tag in LANGUAGE_WORDS:
            logger.debug(f"Audio stream index {stream_data.get('index')} has suitable language tag: {lang_tag}")
            return True

        if lang_tag and lang_tag != "und": # "und" (未定義) 以外で、リストにないものは不適合とする
            logger.debug(
                f"Audio stream index {stream_data.get('index')} has language '{lang_tag}', not in desired list {LANGUAGE_WORDS}. Considered unsuitable by tag."
            )
            return False

        # タグが "und" または存在しない場合のみ言語検出を試みる
        if not lang_tag or lang_tag == "und":
            lang_tag_if_exists = stream_data.get("tags", {}).get("language", "no_tag")
            logger.debug(
                f"Audio stream index {stream_data.get('index')} for {self.media_file.filename} has '{lang_tag_if_exists}' language tag. Attempting language detection."
            )
            try:
                file_duration = (
                    self.media_file.duration if self.media_file.duration > 0 else 0
                )
                # ファイルが非常に短い場合、またはdurationが不明な場合、セグメント数を減らすか、検出をスキップするロジックも検討可能
                if file_duration < 10: # 例: 10秒未満のファイルは検出が不安定な可能性
                    logger.warning(f"File {self.media_file.filename} is very short ({file_duration}s). Language detection might be unreliable or skipped.")
                    # ここで True を返すか、特定の短いファイル用の処理をするか選択
                    # 安全策として、短い場合はタグがなければ不適合とすることもできる
                    return False # 短すぎる場合は検出せず、タグがなければ不適合とする例

                temp_dir_for_detection_str = getattr(self.args, "temp_work_dir", None)
                temp_dir_for_detection = Path(temp_dir_for_detection_str) if temp_dir_for_detection_str else None

                detected_lang = detect_audio_language_multi_segments(
                    self.media_file.path,
                    stream_data,
                    total_media_duration_seconds=int(file_duration),
                    temp_work_dir_override=temp_dir_for_detection,
                ).lower()
                logger.debug(
                    f"Detected language for audio stream index {stream_data.get('index')} of {self.media_file.filename}: {detected_lang}"
                )
                return detected_lang in LANGUAGE_WORDS
            except Exception as det_ex:
                logger.error(
                    f"Language detection failed for audio stream index {stream_data.get('index')} of {self.media_file.filename}: {det_ex}"
                )
                # 検出失敗時は、--allow-no-audio がなければ不適合とする
                # (あるいは、タグが "und" の場合に限り True を返すなどのフォールバックも検討可能)
                return False
        return False # 上記のいずれの条件にも当てはまらない場合 (基本的には到達しないはず)

    def _select_output_subtitle_streams(self):
        if not self.media_file.subtitle_streams:
            self.output_subtitle_streams = []
            return

        suitable_subtitle_streams = []
        for stream in self.media_file.subtitle_streams:
            lang_tag = stream.get("tags", {}).get("language", "").lower().strip()
            if lang_tag and lang_tag in LANGUAGE_WORDS:
                suitable_subtitle_streams.append(stream)
            elif not lang_tag: # 字幕の場合、言語不明は通常不要
                logger.debug(f"Subtitle stream index {stream.get('index')} for {self.media_file.filename} has undetermined or no language tag. Skipping.")
        self.output_subtitle_streams = suitable_subtitle_streams
        logger.debug(f"Selected {len(self.output_subtitle_streams)} subtitle streams for {self.media_file.filename}.")