import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from pprint import pformat
from typing import Optional, List, Dict, Tuple, Type, Any

from loguru import logger

# Utils
from ..utils.ffmpeg_utils import (
    run_cmd,
    detect_audio_language_multi_segments,
)
from ..utils.format_utils import format_timedelta, find_key_in_dictionary


# Domain
from ..domain.media import MediaFile
from ..domain.exceptions import (
    PreprocessingException,
    CRFSearchFailedException,
    SkippedFileException,
    UnexpectedPreprocessingException,
    NoAudioStreamException,
    FileAlreadyEncodedException,
    BitRateTooLowException,
    FormatExcludedException,
    NoStreamsFoundException,
)
from ..domain.temp_models import EncodeInfo

# Config
from ..config.common import BASE_ERROR_DIR, LANGUAGE_WORDS
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

        self.output_video_streams = []
        self.output_audio_streams = []
        self.output_subtitle_streams = []

    def start(self):
        logger.debug(f"PreEncoder start for: {self.media_file.filename}")
        try:
            self._check_if_file_should_be_skipped()
            self._determine_optimal_encoding_options()

        except SkippedFileException:
            raise
        except PreprocessingException as e:
            logger.error(f"Preprocessing error for {self.media_file.filename}: {e}")
            if not self.renamed_file_on_skip_or_error:
                self.move_file_to_error_dir(
                    error_subdir_name=f"preproc_err_{type(e).__name__}"
                )
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error during PreEncoder.start for {self.media_file.filename}: {e}",
                exc_info=True,
            )
            if not self.renamed_file_on_skip_or_error:
                self.move_file_to_error_dir(error_subdir_name="preproc_unexpected_err")
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
            log_level_for_skip = logger.warning
        elif self.encode_stream_count_relevant == 0:
            skip_reason = "No relevant streams to process"
            exception_type = NoStreamsFoundException

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
                    shutil.move(str(self.media_file.path), str(target_skip_path))
                    self.renamed_file_on_skip_or_error = target_skip_path
                    logger.info(f"Moved skipped file to {target_skip_path}")
                except Exception as move_err:
                    logger.error(
                        f"Could not move skipped file {self.media_file.filename} to {target_skip_path}: {move_err}"
                    )
                    self.renamed_file_on_skip_or_error = self.media_file.path
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
            and self.renamed_file_on_skip_or_error != self.media_file.path
        ):
            logger.debug(
                f"File {self.media_file.filename} already moved to {self.renamed_file_on_skip_or_error}. Skipping move_file_to_error_dir to {error_subdir_name}."
            )
            return

        target_error_dir = (
            self.error_dir_base / error_subdir_name / self.media_file.relative_dir
        )
        target_error_dir.mkdir(parents=True, exist_ok=True)
        target_error_path = target_error_dir / self.media_file.filename

        if self.media_file.path.exists():
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
                self.renamed_file_on_skip_or_error = self.media_file.path
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
        self.available_encoders_cfg: Tuple[str] = AVAILABLE_ENCODERS

    def _determine_optimal_encoding_options(self):
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
                logger.error(
                    f"No suitable audio stream for {self.media_file.filename}: {e}. File will be moved to error directory."
                )
                self.move_file_to_error_dir(
                    error_subdir_name=VIDEO_NO_AUDIO_FOUND_ERROR_DIR.name
                )
                raise
        self._select_output_subtitle_streams()

        if self.encode_info_handler.load():
            self.best_crf = self.encode_info_handler.crf
            self.best_encoder = self.encode_info_handler.encoder
            self.is_manual_mode = True
            logger.info(
                f"Loaded encode params from cache for {self.media_file.filename}: Encoder {self.best_encoder}, CRF {self.best_crf}. Treating as manual."
            )
            return

        if self.is_manual_mode:
            self.best_crf = MANUAL_CRF
            self.best_encoder = (
                self.available_encoders_cfg[0]
                if self.available_encoders_cfg
                else AV1_ENCODER
            )
            self.crf_checking_time = timedelta(0)
            logger.info(
                f"Manual mode for {self.media_file.filename}: Using Encoder {self.best_encoder}, CRF {self.best_crf}."
            )
            self.encode_info_handler.dump(
                encoder=self.best_encoder,
                crf=self.best_crf,
                ori_video_path=self.media_file.path.as_posix(),
            )
            return

        crf_search_start_time = datetime.now()
        current_best_ratio_found: Optional[float] = None

        for encoder_candidate in self.available_encoders_cfg:
            try:
                crf_val, encoded_ratio_val = self._perform_crf_search_for_encoder(
                    encoder_candidate
                )
                encoded_ratio_float = encoded_ratio_val / 100.0

                if (
                    current_best_ratio_found is None
                    or encoded_ratio_float < current_best_ratio_found
                ):
                    current_best_ratio_found = encoded_ratio_float
                    self.best_encoder = encoder_candidate
                    self.best_crf = crf_val
                    self.best_ratio = encoded_ratio_float

            except CRFSearchFailedException as e:
                logger.debug(
                    f"CRF search failed for encoder {encoder_candidate} on {self.media_file.filename}: {e}. Trying next encoder if available."
                )
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error during CRF search for {encoder_candidate} on {self.media_file.filename}: {e}",
                    exc_info=True,
                )
                continue

        self.crf_checking_time = datetime.now() - crf_search_start_time

        if not self.best_encoder or self.best_crf == 0:
            logger.warning(
                f"CRF search failed for all configured encoders for {self.media_file.filename}. Using fallback manual settings (CRF: {MANUAL_CRF})."
            )
            self.best_crf = MANUAL_CRF
            self.best_encoder = (
                self.available_encoders_cfg[0]
                if self.available_encoders_cfg
                else AV1_ENCODER
            )
            self.is_manual_mode = True
            self.best_ratio = None
            self.encode_info_handler.dump(
                encoder=self.best_encoder,
                crf=self.best_crf,
                ori_video_path=self.media_file.path.as_posix(),
            )
        else:
            ratio_log_str = (
                f"{self.best_ratio:.2f}" if self.best_ratio is not None else "N/A"
            )
            logger.debug(
                f"Determined best params for {self.media_file.filename}: Encoder {self.best_encoder}, CRF {self.best_crf}, Ratio {ratio_log_str}. Time: {format_timedelta(self.crf_checking_time)}"
            )
            self.encode_info_handler.dump(
                encoder=self.best_encoder,
                crf=self.best_crf,
                ori_video_path=self.media_file.path.as_posix(),
            )

    def _perform_crf_search_for_encoder(self, encoder_to_test: str) -> Tuple[int, int]:
        if (
            self.renamed_file_on_skip_or_error
            and self.renamed_file_on_skip_or_error.exists()
            and self.renamed_file_on_skip_or_error != self.media_file.path
        ):
            logger.info(
                f"CRF search: File {self.media_file.filename} appears to have been moved/renamed to {self.renamed_file_on_skip_or_error}. Skipping CRF search."
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
            temp_dir_for_ab_av1 = str(self.args.temp_work_dir)
            logger.debug(
                f"Using specified temporary directory for ab-av1: {temp_dir_for_ab_av1}"
            )

        cmd_parts = [
            "ab-av1",
            "crf-search",
            "-e",
            encoder_to_test,
            "-i",
            str(self.media_file.path),  # Ensure path is string
            "--sample-every",
            SAMPLE_EVERY,
            "--max-encoded-percent",
            str(MAX_ENCODED_PERCENT),  # Ensure numeric args are strings
            "--min-vmaf",
            str(TARGET_VMAF),  # Ensure numeric args are strings
        ]
        if temp_dir_for_ab_av1:
            cmd_parts.extend(["--temp-dir", temp_dir_for_ab_av1])

        cmd_str = " ".join(
            f'"{part}"' if " " in part else part for part in cmd_parts
        )  # Basic quoting for parts with spaces
        # More robust quoting might be needed if paths/args have special shell characters.
        # For `shell=True` in `run_cmd`, often simpler to just build the full string carefully.
        # Rebuilding cmd_str more carefully for shell=True:
        cmd_str = (
            f'ab-av1 crf-search -e "{encoder_to_test}" -i "{self.media_file.path}" '
            f'--sample-every "{SAMPLE_EVERY}" --max-encoded-percent "{MAX_ENCODED_PERCENT}" '
            f'--min-vmaf "{TARGET_VMAF}"'
        )
        if temp_dir_for_ab_av1:
            cmd_str += f' --temp-dir "{temp_dir_for_ab_av1}"'

        logger.debug(
            f"Executing CRF search for {self.media_file.filename} with {encoder_to_test}: {cmd_str}"
        )

        crf_check_error_dir = (
            self.error_dir_base / "crf_check_errors" / self.media_file.relative_dir
        )
        crf_check_error_dir.mkdir(parents=True, exist_ok=True)

        res = run_cmd(
            cmd_str,
            src_file_for_log=self.media_file.path,
            error_log_dir_for_run_cmd=crf_check_error_dir,
            show_cmd=__debug__,
        )

        if res is None or res.returncode != 0:
            err_msg = f"ab-av1 crf-search command failed. Return code: {res.returncode if res else 'N/A'}."
            if res and res.stderr:
                err_msg += f" Stderr: {res.stderr}"
            logger.debug(err_msg)
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
            logger.info(
                f"CRF search for {encoder_to_test} on {self.media_file.filename} resulted in: CRF {crf}, Ratio {encoded_ratio_percent}%"
            )
            if (
                crf <= 0
                or encoded_ratio_percent <= 0
                or encoded_ratio_percent > MAX_ENCODED_PERCENT + 15
            ):
                raise CRFSearchFailedException(
                    f"CRF search for {encoder_to_test} yielded potentially invalid results: CRF {crf}, Ratio {encoded_ratio_percent}%"
                )
            return crf, encoded_ratio_percent
        else:
            err_msg = f"Could not parse CRF and/or Ratio from ab-av1 output for {encoder_to_test}. Output: {res.stdout}"
            logger.error(err_msg)
            debug_ab_av1_output_path = (
                crf_check_error_dir / f"{self.media_file.filename}.ab_av1_output.txt"
            )
            with debug_ab_av1_output_path.open("w", encoding="utf-8") as f_debug:
                f_debug.write(
                    f"Command: {cmd_str}\n\nStdout:\n{res.stdout}\n\nStderr:\n{res.stderr if res else 'N/A'}"
                )
            raise CRFSearchFailedException(err_msg)

    def _select_output_video_streams(self):
        if not self.media_file.video_streams:
            logger.warning(
                f"No video streams found in {self.media_file.filename} by MediaFile analysis."
            )
            self.output_video_streams = []
            return

        if len(self.media_file.video_streams) == 1:
            stream = self.media_file.video_streams[0]
            if (
                "avg_frame_rate" in stream
                and stream.get("codec_name") not in SKIP_VIDEO_CODEC_NAMES
            ):
                self.output_video_streams = [stream]
            else:
                logger.warning(
                    f"Single video stream in {self.media_file.filename} is unsuitable (no fps or excluded codec). No video output."
                )
                self.output_video_streams = []
            return

        suitable_video_streams = []
        for stream in self.media_file.video_streams:
            codec_name = stream.get("codec_name", "").lower()
            if (
                "avg_frame_rate" in stream
                and stream["avg_frame_rate"] != "0/0"
                and codec_name not in SKIP_VIDEO_CODEC_NAMES
            ):
                suitable_video_streams.append(stream)
            else:
                logger.debug(
                    f"Skipping video stream index {stream.get('index')} for {self.media_file.filename} due to missing/invalid fps or excluded codec ({codec_name})."
                )

        if not suitable_video_streams:
            logger.warning(
                f"No suitable video streams found after filtering for {self.media_file.filename}."
            )
        self.output_video_streams = suitable_video_streams

    def _select_output_audio_streams(self):
        if not self.media_file.audio_streams:
            raise NoAudioStreamException(
                f"No audio streams at all in {self.media_file.filename}."
            )

        if len(self.media_file.audio_streams) == 1:
            stream = self.media_file.audio_streams[0]
            if self._is_audio_stream_language_suitable(stream):
                self.output_audio_streams = [stream]
            else:
                raise NoAudioStreamException(
                    f"Single audio stream in {self.media_file.filename} does not match desired languages."
                )
            return

        suitable_audio_streams = []
        for stream in self.media_file.audio_streams:
            if "sample_rate" in stream:
                try:
                    sample_rate = int(float(stream.get("sample_rate", 0)))
                    if sample_rate < 1000:
                        logger.debug(
                            f"Skipping audio stream index {stream.get('index')} for {self.media_file.filename}: low sample rate {sample_rate}."
                        )
                        continue
                except ValueError:
                    logger.debug(
                        f"Skipping audio stream index {stream.get('index')} for {self.media_file.filename}: invalid sample rate format."
                    )
                    continue

            if self._is_audio_stream_language_suitable(stream):
                suitable_audio_streams.append(stream)

        if not suitable_audio_streams:
            raise NoAudioStreamException(
                f"No audio streams match desired languages after filtering for {self.media_file.filename}."
            )

        self.output_audio_streams = suitable_audio_streams
        logger.debug(
            f"Selected {len(self.output_audio_streams)} audio streams for {self.media_file.filename}."
        )

    def _is_audio_stream_language_suitable(self, stream_data: Dict) -> bool:
        lang_tag = stream_data.get("tags", {}).get("language", "").lower()
        if lang_tag and lang_tag in LANGUAGE_WORDS:
            return True
        if lang_tag and lang_tag != "und":
            logger.debug(
                f"Audio stream index {stream_data.get('index')} has language '{lang_tag}', not in desired list. It is considered unsuitable."
            )
            return False

        if not lang_tag or lang_tag == "und":
            logger.info(
                f"Audio stream index {stream_data.get('index')} for {self.media_file.filename} has no definitive language tag. Attempting language detection."
            )
            try:
                file_duration = (
                    self.media_file.duration if self.media_file.duration > 0 else 0
                )
                temp_dir_for_detection = (
                    getattr(self.args, "temp_work_dir", None) if self.args else None
                )
                detected_lang = detect_audio_language_multi_segments(
                    self.media_file.path,
                    stream_data,
                    total_media_duration_seconds=int(file_duration),
                    temp_work_dir_override=temp_dir_for_detection,
                ).lower()
                logger.info(
                    f"Detected language for audio stream index {stream_data.get('index')} of {self.media_file.filename}: {detected_lang}"
                )
                return detected_lang in LANGUAGE_WORDS
            except Exception as det_ex:
                logger.error(
                    f"Language detection failed for audio stream index {stream_data.get('index')} of {self.media_file.filename}: {det_ex}"
                )
                return False

        return False

    def _select_output_subtitle_streams(self):
        if not self.media_file.subtitle_streams:
            self.output_subtitle_streams = []
            return

        suitable_subtitle_streams = []
        for stream in self.media_file.subtitle_streams:
            lang_tag = stream.get("tags", {}).get("language", "").lower()
            if lang_tag and lang_tag in LANGUAGE_WORDS:
                suitable_subtitle_streams.append(stream)
            elif not lang_tag or lang_tag == "und":
                logger.debug(f"Subtitle stream index {stream.get('index')} for {self.media_file.filename} has undetermined or no language tag. Skipping.")

        self.output_subtitle_streams = suitable_subtitle_streams
        logger.debug(f"Selected {len(self.output_subtitle_streams)} subtitle streams for {self.media_file.filename}.")