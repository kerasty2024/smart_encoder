"""
This module defines the VideoEncoder, the primary service for high-quality,
automated video encoding. It handles the main encoding logic, including stream
selection, quality control, and error handling.
"""

import os
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta
from fractions import Fraction
from typing import Any, Optional

import yaml
from loguru import logger

# Import configuration constants for various settings.
from ..config.common import (
    COMMAND_TEXT,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_ERROR_PERMANENT,
)
from ..config.video import (
    AUDIO_OPUS_CODECS,
    COMPLETED_RAW_DIR,
    ENCODERS as VIDEO_ENCODERS_CONFIG,
    MANUAL_CRF,
    MAX_CRF,
    OPUS_ENCODER,
    SUBTITLE_MKV_CODECS,
    TARGET_VMAF,
    VIDEO_COMMENT_ENCODED,
    VIDEO_OUT_DIR_ROOT,
)

# Import domain models, exceptions, and other services.
from ..domain.exceptions import EncodingException, MP4MKVEncodeFailException
from ..domain.media import MediaFile
from ..services.preprocessing_service import PreVideoEncoder
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import format_timedelta, formatted_size
from .encoder_base import Encoder


class VideoEncoder(Encoder):
    """
    The main video encoding service for standard, high-quality encodes.

    This class orchestrates the standard video encoding pipeline. It collaborates
    with the `PreVideoEncoder` to obtain optimal encoding settings (like CRF value
    and stream selections) and then executes the final FFmpeg encoding command.

    Key Features:
    - Works with `PreVideoEncoder` for automated quality-based encoding, which
      aims for a specific perceptual quality (VMAF) score.
    - Handles complex stream mapping for video, audio, and subtitles, intelligently
      deciding whether to copy or re-encode each stream.
    - If encoding to an MP4 container fails (often due to incompatible subtitle
      formats like PGS), it automatically retries by encoding to a more flexible
      MKV container.
    - If the final encoded file is larger than the original source, it intelligently
      increases the CRF value (reducing quality slightly) and re-encodes to
      ensure the output file is smaller.
    """

    # The pre_encoder is a required component for the VideoEncoder.
    pre_encoder: PreVideoEncoder

    def __init__(self, media_file: MediaFile, args: Any):
        """
        Initializes the VideoEncoder instance.

        Args:
            media_file: The `MediaFile` object representing the source video to be encoded.
            args: The command-line arguments passed to the application.
        """
        super().__init__(media_file, args)

        # --- Configure paths specific to standard video encoding ---
        self.encoded_dir = (
            VIDEO_OUT_DIR_ROOT / self.original_media_file.relative_dir
        ).resolve()
        # Default output is an MP4 file. This may be changed to MKV during the process.
        self.encoded_file = self.encoded_dir / f"{self.original_media_file.stem}.mp4"
        self.success_log_output_dir = self.encoded_dir
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR / self.original_media_file.relative_dir
        ).resolve()

        # --- Initialize the Pre-encoder and link its state manager ---
        # The VideoEncoder relies on a PreVideoEncoder instance to get its settings.
        self.pre_encoder = PreVideoEncoder(
            media_file, self.args.manual_mode, args=self.args
        )
        # The job state (EncodeInfo) is managed by the pre-encoder and shared here.
        self.encode_info = self.pre_encoder.encode_info_handler

        # --- Initialize command parts for FFmpeg ---
        self.video_map_cmd_part = ""
        self.audio_map_cmd_part = ""
        self.subtitle_map_cmd_part = ""

    def encode(self):
        """
        The main entry point for the video encoding process.

        This method retrieves settings determined by the pre-encoder, validates them,
        and then triggers the FFmpeg encoding process by calling `_ffmpeg_encode_video`.
        """
        # 1. Retrieve the encoder and CRF value from the completed pre-encoding stage.
        self.encoder_codec_name = self.encode_info.encoder
        self.crf = self.encode_info.crf

        if not self.encoder_codec_name or self.crf is None:
            # This indicates a critical failure in the pre-encoding stage.
            error_msg = f"Pre-encoder did not provide a valid encoder/CRF. Encoder: '{self.encoder_codec_name}', CRF: {self.crf}"
            logger.error(error_msg)
            self.failed_action("", error_msg, -1, is_retryable_error=False)
            raise EncodingException(error_msg)

        # 2. Ensure stream selections are available from the pre-encoder.
        # This is a safety check; if these are missing, something went wrong in the pre-encoding stage.
        if not (
            self.pre_encoder.output_video_streams
            or self.pre_encoder.output_audio_streams
            or self.pre_encoder.output_subtitle_streams
        ) and not (
            self.args
            and getattr(self.args, "allow_no_audio", False)
            and self.pre_encoder.output_video_streams
        ):
            if self.encode_info.pre_encoder_data:
                # Attempt to restore stream selections from the saved state file.
                logger.debug(
                    f"Restoring stream selections from pre_encoder_data for {self.original_media_file.filename} in VideoEncoder.encode."
                )
                self.pre_encoder.output_video_streams = (
                    self.encode_info.pre_encoder_data.get("output_video_streams", [])
                )
                self.pre_encoder.output_audio_streams = (
                    self.encode_info.pre_encoder_data.get("output_audio_streams", [])
                )
                self.pre_encoder.output_subtitle_streams = (
                    self.encode_info.pre_encoder_data.get("output_subtitle_streams", [])
                )
                # Re-check after restoring. If still no streams, it's a failure.
                if not (
                    self.pre_encoder.output_video_streams
                    or self.pre_encoder.output_audio_streams
                    or self.pre_encoder.output_subtitle_streams
                ) and not (
                    self.args
                    and getattr(self.args, "allow_no_audio", False)
                    and self.pre_encoder.output_video_streams
                ):
                    error_msg = (
                        f"No processable streams were selected or could be restored."
                    )
                    logger.error(error_msg)
                    self.failed_action("", error_msg, -1, is_retryable_error=False)
                    raise EncodingException(error_msg)
            else:
                error_msg = f"Pre-encoder data is missing, and no stream selections are available."
                logger.error(error_msg)
                self.failed_action("", error_msg, -1, is_retryable_error=False)
                raise EncodingException(error_msg)

        # 3. Trigger the FFmpeg encoding process. The default target container is .mp4.
        self.encoded_file = self.encoded_dir / f"{self.original_media_file.stem}.mp4"
        try:
            self._ffmpeg_encode_video()
        except MP4MKVEncodeFailException as e:
            # This specific exception is raised when both MP4 and MKV attempts fail.
            # We just re-raise it to be handled by the pipeline.
            raise
        except EncodingException as e:
            # Any other encoding exception is also passed up.
            raise

    def _check_and_handle_oversized(self, attempt=1) -> bool:
        """
        Checks if the encoded file is larger than the original. If so, it
        intelligently increases the CRF value and triggers a re-encode.

        This is a recursive-like process that attempts to shrink the file until it's
        smaller than the original or until it hits a maximum CRF limit, ensuring
        that the encoding process doesn't result in bloated files.

        Args:
            attempt: The current retry attempt number, used for logging.

        Returns:
            True if the file size is acceptable, False if the file remains oversized
            after all attempts.
        """
        if not self.encoded_file.exists():
            # This should not happen if encoding was successful, so it's a critical error.
            logger.error(
                f"_check_and_handle_oversized: Encoded file {self.encoded_file} does not exist."
            )
            self.no_error = False
            self.encode_info.dump(
                status=JOB_STATUS_ERROR_PERMANENT,
                last_error_message="Encoded file missing for oversized check.",
            )
            return False

        current_encoded_size = self.encoded_file.stat().st_size
        # The main condition: if the new file is bigger than the old one.
        if current_encoded_size > self.original_media_file.size:
            logger.warning(
                f"Attempt {attempt}: File is oversized. Original: {formatted_size(self.original_media_file.size)}, "
                f"Encoded: {formatted_size(current_encoded_size)} ({current_encoded_size / self.original_media_file.size:.2%} of original), "
                f"CRF: {self.crf}"
            )
            self.encoded_file.unlink(missing_ok=True)  # Delete the oversized file.

            # If we've already hit the maximum allowed CRF, fail permanently.
            if self.crf == MAX_CRF:
                error_msg = f"Final attempt with max CRF ({MAX_CRF}) still resulted in an oversized file. Cannot reduce size further."
                logger.error(error_msg)
                self.no_error = False
                self.move_to_oversized_error_dir()
                self.encode_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message=error_msg,
                    crf=self.crf,
                )
                return False

            # Intelligently increase CRF based on how much larger the file is.
            # A bigger oversize ratio results in a larger CRF increment.
            oversize_ratio = current_encoded_size / self.original_media_file.size
            crf_increment = int(round(3 + (oversize_ratio - 1) * 10))
            crf_increment = max(3, crf_increment)  # Ensure the increment is at least 3.
            new_crf = (self.crf or MANUAL_CRF) + crf_increment

            # Cap the new CRF at the absolute maximum.
            if new_crf > MAX_CRF:
                logger.warning(
                    f"Calculated new CRF ({new_crf}) exceeds the absolute limit. "
                    f"Capping at {MAX_CRF} for a final attempt."
                )
                new_crf = MAX_CRF

            self.crf = new_crf
            logger.info(
                f"Increased CRF to {self.crf} for {self.original_media_file.filename} (Oversized Attempt {attempt + 1})."
            )

            # Update the state file with the new CRF and a note about the retry.
            self.encode_info.dump(
                crf=self.crf,
                last_error_message=f"Oversized, retrying with CRF {self.crf} (Attempt {attempt+1})",
            )

            try:
                # Trigger the re-encode with the new CRF.
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
                    # Recursively call this function to check the new file's size.
                    return self._check_and_handle_oversized(attempt=attempt + 1)
                else:
                    logger.error(
                        f"Re-encoding attempt {attempt+1} for oversized file failed (ffmpeg error)."
                    )
                    return False
            except Exception as e:
                logger.error(
                    f"Exception during oversized re-encode attempt {attempt+1} for {self.original_media_file.filename}: {e}"
                )
                self.no_error = False
                if (
                    self.original_media_file.path.exists()
                    and not self.current_error_output_path
                ):
                    self.move_to_oversized_error_dir()
                self.encode_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message=f"Exception in oversized retry: {e}",
                    crf=self.crf,
                )
                return False
        else:
            # File size is acceptable, the check is successful.
            logger.debug(
                f"File {self.encoded_file.name} is not oversized. Size: {formatted_size(current_encoded_size)}."
            )
            return True

    def move_to_oversized_error_dir(self):
        """Moves the original file to a specific error directory for unfixably oversized files."""
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
                    logger.error(
                        f"Could not move unfixably oversized original file {self.original_media_file.filename}: {move_e}"
                    )
        else:
            logger.warning(
                f"Original file {self.original_media_file.filename} not found for move_to_oversized_error_dir."
            )
            self.current_error_output_path = target_error_path

    def _set_metadata_comment(self, update_dic: Optional[dict] = None):
        """
        Generates the detailed metadata comment for the encoded video file.

        This comment is embedded in the output file and contains useful information
        for identifying the file and its encoding parameters, which helps prevent
        re-encoding and aids in debugging.

        Args:
            update_dic: An optional dictionary to add or overwrite fields in the comment.
        """
        comment_data = {
            "comment_tag": VIDEO_COMMENT_ENCODED,
            "encoder_options": {
                "codec": self.encoder_codec_name or "N/A",
                "crf": self.crf if self.crf is not None else "N/A",
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
            "encoding_software_config": {
                "configured_encoders": list(VIDEO_ENCODERS_CONFIG)
            },
        }
        if update_dic:
            comment_data.setdefault("process_notes", {}).update(update_dic)

        # Convert the dictionary to a compact, single-line YAML string.
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
        """
        The core FFmpeg execution logic.

        This method builds the complete FFmpeg command, runs it, and handles the
        MP4-to-MKV fallback logic if the initial encode fails.
        """
        # 1. Build all parts of the FFmpeg command based on the selected streams and settings.
        self._build_ffmpeg_stream_maps()
        self._set_metadata_comment(update_log_comment_dict)
        self._build_encode_command()

        # 2. Update job status to 'encoding' and run the command.
        try:
            cmd_str_for_info = (
                subprocess.list2cmdline(self.encode_cmd_list)
                if os.name == "nt"
                else " ".join(shlex.quote(s) for s in self.encode_cmd_list)
            )
        except Exception:
            cmd_str_for_info = " ".join(map(str, self.encode_cmd_list))

        self.encode_info.dump(
            status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
            ffmpeg_command=cmd_str_for_info,
            temp_output_path=str(self.encoded_file),
            encoder=self.encoder_codec_name,
            crf=self.crf,
        )

        show_cmd_output = __debug__
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            self.encode_cmd_list,
            src_file_for_log=self.original_media_file.path,
            error_log_dir_for_run_cmd=self.error_dir_base,
            show_cmd=show_cmd_output,
            cmd_log_file_path=cmd_log_file_path_val,
        )

        # 3. Process the result of the FFmpeg command.
        if res and res.returncode == 0:
            # --- Success Case ---
            logger.debug(
                f"Initial FFmpeg command successful for {self.encoded_file.name}."
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
                    logger.warning(
                        f"Could not set mtime for {self.encoded_file.name}: {utime_err}"
                    )

            if self.encoded_file.exists():
                # After a successful encode, check if the file is oversized.
                if not is_oversized_retry:
                    self._check_and_handle_oversized()
            else:
                error_msg = f"FFmpeg reported success but the output file is missing."
                logger.error(error_msg)
                self.failed_action(
                    res.stdout,
                    f"{res.stderr}\n{error_msg}",
                    res.returncode,
                    is_retryable_error=True,
                )
                raise EncodingException(error_msg)

        elif self.encoded_file.suffix.lower() == ".mp4" and not is_oversized_retry:
            # --- MP4 Failure, MKV Retry Case ---
            logger.warning(
                f"MP4 encoding failed for {self.original_media_file.path.name}. RC: {res.returncode if res else 'N/A'}. This can happen with certain subtitle types. Retrying with a more flexible .mkv container."
            )
            logger.debug(f"MP4 fail Stderr: {res.stderr if res else 'N/A'}")
            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)

            # Switch the target file to MKV and rebuild the command.
            self.encoded_file = self.encoded_file.with_suffix(".mkv")
            self._build_ffmpeg_stream_maps()
            self._set_metadata_comment(update_log_comment_dict)
            self._build_encode_command()

            try:
                cmd_str_for_info_mkv = (
                    subprocess.list2cmdline(self.encode_cmd_list)
                    if os.name == "nt"
                    else " ".join(shlex.quote(s) for s in self.encode_cmd_list)
                )
            except Exception:
                cmd_str_for_info_mkv = " ".join(map(str, self.encode_cmd_list))

            self.encode_info.dump(
                status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
                ffmpeg_command=cmd_str_for_info_mkv,
                temp_output_path=str(self.encoded_file),
                last_error_message="MP4 failed, retrying MKV.",
            )
            logger.info(f"Retrying with MKV container: {self.encoded_file.name}")
            res_mkv = run_cmd(
                self.encode_cmd_list,
                src_file_for_log=self.original_media_file.path,
                error_log_dir_for_run_cmd=self.error_dir_base,
                show_cmd=show_cmd_output,
                cmd_log_file_path=cmd_log_file_path_val,
            )

            if res_mkv and res_mkv.returncode == 0:
                # MKV retry was successful.
                logger.debug(f"MKV encoding successful for {self.encoded_file.name}.")
                self.no_error = True
                if self.keep_mtime and self.encoded_file.exists():
                    try:
                        os.utime(
                            self.encoded_file,
                            (
                                datetime.now().timestamp(),
                                self.original_media_file.path.stat().st_mtime,
                            ),
                        )
                    except Exception as utime_err:
                        logger.warning(
                            f"Could not set mtime for MKV {self.encoded_file.name}: {utime_err}"
                        )

                if self.encoded_file.exists():
                    self._check_and_handle_oversized()  # Also check if the MKV is oversized.
                else:
                    error_msg_mkv = (
                        f"MKV FFmpeg reported success but the output file is missing."
                    )
                    logger.error(error_msg_mkv)
                    self.failed_action(
                        res_mkv.stdout,
                        f"{res_mkv.stderr}\n{error_msg_mkv}",
                        res_mkv.returncode,
                        is_retryable_error=True,
                    )
                    raise EncodingException(error_msg_mkv)
            else:
                # Both MP4 and MKV failed. This is a hard failure.
                logger.error(
                    f"MKV encoding also failed for {self.original_media_file.path.name}."
                )
                if self.encoded_file.exists():
                    self.encoded_file.unlink(missing_ok=True)
                self.failed_action(
                    res_mkv.stdout if res_mkv else (res.stdout if res else ""),
                    res_mkv.stderr if res_mkv else (res.stderr if res else ""),
                    res_mkv.returncode if res_mkv else (res.returncode if res else -1),
                    is_retryable_error=True,
                )
                raise MP4MKVEncodeFailException(
                    f"Both MP4 and MKV encoding attempts failed for {self.original_media_file.path.name}"
                )
        else:
            # --- General Failure Case ---
            error_details = f"ffmpeg command failed for {self.encoded_file.name}. RC: {res.returncode if res else 'N/A'}"
            if is_oversized_retry:
                error_details += " (during an oversized re-encode attempt)"
            logger.error(error_details)

            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)  # Clean up partial file.

            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True,
            )
            raise EncodingException(error_details)

    def _build_ffmpeg_stream_maps(self):
        """
        Builds the complex `-map` and `-c` parts of the FFmpeg command.

        This method iterates through the video, audio, and subtitle streams that were
        selected by the pre-encoder and constructs the appropriate command-line flags
        to either copy them directly (`-c copy`) or re-encode them with a specific codec.
        This is one of the most critical parts of the encoding logic.
        """
        # --- Build Video Map ---
        _video_map_cmd = ""
        max_fps = 240
        if not self.pre_encoder.output_video_streams:
            logger.warning(
                f"No video streams selected for encoding {self.original_media_file.filename}."
            )
        else:
            # We currently only process the first selected video stream.
            video_stream = self.pre_encoder.output_video_streams[0]
            fps_str = "24"  # Default FPS if parsing fails.
            if (
                "avg_frame_rate" in video_stream
                and video_stream["avg_frame_rate"] != "0/0"
            ):
                try:
                    # Use Fraction for precise FPS handling.
                    fps_fraction = Fraction(str(video_stream.get("avg_frame_rate")))
                    if fps_fraction > 0 and fps_fraction <= max_fps:
                        fps_str = str(
                            float(fps_fraction)
                        )  # Convert to float string for command.
                except (ZeroDivisionError, ValueError) as e:
                    logger.warning(
                        f"Error parsing avg_frame_rate '{video_stream.get('avg_frame_rate')}'. Using default FPS."
                    )
            stream_index = int(video_stream.get("index", 0))
            _video_map_cmd += f'-map 0:{stream_index} -r "{fps_str}" '
        self.video_map_cmd_part = _video_map_cmd.strip()

        # --- Build Audio Map ---
        _audio_map_cmd = ""
        audio_output_idx = 0
        if not self.pre_encoder.output_audio_streams:
            logger.debug(
                f"No audio streams selected for {self.original_media_file.filename}."
            )
        else:
            for audio_stream in self.pre_encoder.output_audio_streams:
                stream_index = int(audio_stream.get("index", 0))
                input_codec_name = audio_stream.get("codec_name", "").lower()
                acodec_name = (
                    "copy"  # Default action: copy the stream without re-encoding.
                )
                abitrate_cmd = ""
                # If the audio codec is inefficient (e.g., PCM, FLAC), re-encode it to the efficient Opus codec.
                if (
                    input_codec_name in AUDIO_OPUS_CODECS
                    and audio_stream.get("channels", 2) <= 2
                ):
                    acodec_name = OPUS_ENCODER
                    # Determine target bitrate for Opus based on original bitrate.
                    original_bitrate = int(audio_stream.get("bit_rate", 0))
                    target_opus_bitrate = (
                        min(original_bitrate, 500 * 1000)
                        if original_bitrate > 0
                        else 250 * 1000
                    )
                    abitrate_cmd = f"-b:a:{audio_output_idx} {target_opus_bitrate} "
                _audio_map_cmd += f"-map 0:{stream_index} -c:a:{audio_output_idx} {acodec_name} {abitrate_cmd}"
                audio_output_idx += 1
        self.audio_map_cmd_part = _audio_map_cmd.strip()

        # --- Build Subtitle Map ---
        _subtitle_map_cmd = ""
        subtitle_output_idx = 0
        if not self.pre_encoder.output_subtitle_streams:
            logger.debug(
                f"No subtitle streams selected for {self.original_media_file.filename}."
            )
        else:
            for subtitle_stream in self.pre_encoder.output_subtitle_streams:
                stream_index = int(subtitle_stream.get("index", 0))
                input_subtitle_codec = subtitle_stream.get("codec_name", "").lower()
                is_mkv_target = self.encoded_file.suffix.lower() == ".mkv"

                # Logic to decide if subtitles should be copied or converted.
                if is_mkv_target:
                    # MKV is very flexible; copy most subtitle types.
                    scodec_name = "copy"
                else:  # MP4 Target
                    # MP4 is less flexible. Text-based subtitles are preferred.
                    if input_subtitle_codec in ["mov_text", "tx3g"]:
                        scodec_name = "copy"
                    elif input_subtitle_codec in [
                        "subrip",
                        "srt",
                        "ass",
                        "ssa",
                        "webvtt",
                    ]:
                        # Convert common text formats to the MP4-native mov_text.
                        scodec_name = "mov_text"
                    else:
                        # For bitmap subtitles (PGS, VobSub), attempt conversion.
                        # This will likely fail for MP4, triggering the MKV retry logic.
                        logger.debug(
                            f"Attempting to convert bitmap subtitle '{input_subtitle_codec}' to mov_text for MP4. This is expected to fail and trigger an MKV retry."
                        )
                        scodec_name = "mov_text"
                _subtitle_map_cmd += (
                    f"-map 0:{stream_index} -c:s:{subtitle_output_idx} {scodec_name} "
                )
                subtitle_output_idx += 1
        self.subtitle_map_cmd_part = _subtitle_map_cmd.strip()

    def _build_encode_command(self):
        """Builds the final FFmpeg command list from all its constituent parts."""
        input_path_str = str(self.original_media_file.path.resolve())
        output_path_str = str(self.encoded_file.resolve())

        # Start with the basic ffmpeg command.
        cmd_list = ["ffmpeg", "-y", "-i", input_path_str]
        # Add video encoding options.
        cmd_list.extend(["-c:v", self.encoder_codec_name, "-crf", str(self.crf)])
        # Add the stream mapping parts we built earlier.
        if self.video_map_cmd_part:
            cmd_list.extend(shlex.split(self.video_map_cmd_part))
        cmd_list.extend(["-metadata", f"comment={self.encoded_comment_text}"])
        if self.audio_map_cmd_part:
            cmd_list.extend(shlex.split(self.audio_map_cmd_part))
        if self.subtitle_map_cmd_part:
            cmd_list.extend(shlex.split(self.subtitle_map_cmd_part))
        # Add the final output path.
        cmd_list.append(output_path_str)
        self.encode_cmd_list = cmd_list

        display_cmd = (
            subprocess.list2cmdline(self.encode_cmd_list)
            if os.name == "nt"
            else " ".join(shlex.quote(s) for s in self.encode_cmd_list)
        )
        logger.debug(
            f"Built ffmpeg command for {self.original_media_file.filename}:\n{display_cmd}"
        )

    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        """
        Overrides the base `write_success_log` to add video-specific information
        from the pre-encoding stage, such as VMAF target and CRF search time.
        """
        pre_encoder_data = self.encode_info.pre_encoder_data or {}
        crf_seconds = pre_encoder_data.get("crf_checking_time_seconds")
        video_specific_log_data = {
            "pre_encode_info": {
                "is_manual_mode": pre_encoder_data.get("is_manual_mode", "N/A"),
                "estimated_size_ratio_percent": round(
                    pre_encoder_data.get("best_ratio", 0) * 100, 2
                )
                if pre_encoder_data.get("best_ratio") is not None
                else None,
                "crf_checking_time_formatted": format_timedelta(
                    timedelta(seconds=crf_seconds if crf_seconds is not None else 0)
                ),
                "target_vmaf_for_pre_encode": TARGET_VMAF,
            },
        }
        if update_dic:
            video_specific_log_data.update(update_dic)
        # Call the parent method with the additional video-specific data.
        super().write_success_log(
            log_date_in_filename=log_date_in_filename,
            update_dic=video_specific_log_data,
        )