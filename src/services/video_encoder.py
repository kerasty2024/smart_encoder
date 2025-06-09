import os
import shlex
import shutil
import subprocess
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from loguru import logger

from ..config.common import (
    BASE_ERROR_DIR,
    COMMAND_TEXT,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_ERROR_PERMANENT,
)
from ..config.video import (
    AUDIO_OPUS_CODECS,
    COMPLETED_RAW_DIR,
    ENCODERS as VIDEO_ENCODERS_CONFIG,
    MANUAL_CRF,
    MANUAL_CRF_INCREMENT_PERCENT,
    MAX_CRF,
    OPUS_ENCODER,
    SUBTITLE_MKV_CODECS,
    TARGET_VMAF,
    VIDEO_COMMENT_ENCODED,
    VIDEO_OUT_DIR_ROOT,
)
from ..domain.exceptions import EncodingException, MP4MKVEncodeFailException
from ..domain.media import MediaFile
from ..services.preprocessing_service import PreVideoEncoder
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import format_timedelta, formatted_size
from .encoder_base import Encoder


class VideoEncoder(Encoder):
    pre_encoder: PreVideoEncoder

    def __init__(self, media_file: MediaFile, args: Any):
        super().__init__(media_file, args)
        self.encoded_dir = (
            VIDEO_OUT_DIR_ROOT / self.original_media_file.relative_dir
        ).resolve()
        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )
        self.success_log_output_dir = self.encoded_dir
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR / self.original_media_file.relative_dir
        ).resolve()
        self.pre_encoder = PreVideoEncoder(
            media_file, self.args.manual_mode, args=self.args
        )
        self.encode_info = self.pre_encoder.encode_info_handler
        self.video_map_cmd_part = ""
        self.audio_map_cmd_part = ""
        self.subtitle_map_cmd_part = ""

    def encode(self):
        self.encoder_codec_name = self.encode_info.encoder
        self.crf = self.encode_info.crf

        if not self.encoder_codec_name or self.crf is None:
            error_msg = f"Pre-encoder did not set valid encoder/CRF for {self.original_media_file.filename}. Encoder: '{self.encoder_codec_name}', CRF: {self.crf}"
            logger.error(error_msg)
            self.failed_action("", error_msg, -1, is_retryable_error=False)
            raise EncodingException(error_msg)

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
                if not (
                    self.pre_encoder.output_video_streams
                    or self.pre_encoder.output_audio_streams
                    or self.pre_encoder.output_subtitle_streams
                ) and not (
                    self.args
                    and getattr(self.args, "allow_no_audio", False)
                    and self.pre_encoder.output_video_streams
                ):
                    error_msg = f"No processable streams selected or restored for {self.original_media_file.filename}."
                    logger.error(error_msg)
                    self.failed_action("", error_msg, -1, is_retryable_error=False)
                    raise EncodingException(error_msg)
            else:
                error_msg = f"Pre-encoder data (including stream selections) is missing for {self.original_media_file.filename}, and no streams on pre_encoder instance attributes."
                logger.error(error_msg)
                self.failed_action("", error_msg, -1, is_retryable_error=False)
                raise EncodingException(error_msg)

        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )
        try:
            self._ffmpeg_encode_video()
        except MP4MKVEncodeFailException as e:
            raise
        except EncodingException as e:
            raise

    def _check_and_handle_oversized(self, attempt=1) -> bool:
        if not self.encoded_file.exists():
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
        if current_encoded_size > self.original_media_file.size:
            ABSOLUTE_MAX_CRF = 63  # The absolute maximum CRF value to try
            logger.warning(
                f"Attempt {attempt}: File is oversized. Original: {formatted_size(self.original_media_file.size)}, "
                f"Encoded: {formatted_size(current_encoded_size)} ({current_encoded_size / self.original_media_file.size:.2%} of original), "
                f"CRF: {self.crf}"
            )
            self.encoded_file.unlink(missing_ok=True)

            # If the previous attempt was already at the max CRF, it's a permanent failure.
            if self.crf == ABSOLUTE_MAX_CRF:
                error_msg = f"Final attempt with max CRF ({ABSOLUTE_MAX_CRF}) still resulted in an oversized file. Cannot reduce size further."
                logger.error(error_msg)
                self.no_error = False
                self.move_to_oversized_error_dir()
                self.encode_info.dump(
                    status=JOB_STATUS_ERROR_PERMANENT,
                    last_error_message=error_msg,
                    crf=self.crf,
                )
                return False

            # --- ▼▼▼ CRF INCREMENT LOGIC MODIFIED ▼▼▼ ---
            oversize_ratio = current_encoded_size / self.original_media_file.size
            crf_increment = int(round(3 + (oversize_ratio - 1) * 10))
            # Ensure the increment is at least 3
            crf_increment = max(3, crf_increment)
            new_crf = (self.crf or MANUAL_CRF) + crf_increment

            # Cap the new CRF if it exceeds the limit, for a final attempt.
            if new_crf > ABSOLUTE_MAX_CRF:
                logger.warning(
                    f"Calculated CRF ({new_crf}) exceeds the limit. "
                    f"Capping at {ABSOLUTE_MAX_CRF} for a final attempt."
                )
                new_crf = ABSOLUTE_MAX_CRF
            # --- ▲▲▲ CRF INCREMENT LOGIC MODIFIED ▲▲▲ ---

            self.crf = new_crf
            logger.info(
                f"Increased CRF to {self.crf} for {self.original_media_file.filename} (Oversized Attempt {attempt + 1})."
            )

            self.encode_info.dump(
                crf=self.crf,
                last_error_message=f"Oversized, retrying with CRF {self.crf} (Attempt {attempt+1})",
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
                    # Recursive call without max_attempts
                    return self._check_and_handle_oversized(attempt=attempt + 1)
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
                    logger.error(
                        f"Could not move unfixably oversized original file {self.original_media_file.filename}: {move_e}"
                    )
        else:
            logger.warning(
                f"Original file {self.original_media_file.filename} not found for move_to_oversized_error_dir."
            )
            self.current_error_output_path = target_error_path

    def _set_metadata_comment(self, update_dic: Optional[dict] = None):
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
        self._build_encode_command()  # This now sets self.encode_cmd_list

        # Ensure ffmpeg_command in EncodeInfo is a string for logging/display
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

        res = run_cmd(  # Pass the list
            self.encode_cmd_list,
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
                    logger.warning(
                        f"Could not set mtime for {self.encoded_file.name}: {utime_err}"
                    )

            if self.encoded_file.exists():
                if not is_oversized_retry:
                    if not self._check_and_handle_oversized():
                        pass
            else:
                error_msg = f"ffmpeg reported success but output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(
                    res.stdout,
                    f"{res.stderr}\n{error_msg}",
                    res.returncode,
                    is_retryable_error=True,
                )
                raise EncodingException(error_msg)

        elif self.encoded_file.suffix.lower() == ".mp4" and not is_oversized_retry:
            logger.warning(
                f"MP4 encoding failed for {self.original_media_file.path.name}. "
                f"RC: {res.returncode if res else 'N/A'}. Retrying with .mkv."
            )
            logger.debug(f"MP4 fail Stderr: {res.stderr if res else 'N/A'}")
            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)

            self.encoded_file = self.encoded_file.with_suffix(".mkv")
            self._build_ffmpeg_stream_maps()  # Rebuild maps for MKV target
            self._set_metadata_comment(
                update_log_comment_dict
            )  # Comment might not need to change, but consistent
            self._build_encode_command()  # Rebuilds self.encode_cmd_list for MKV

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
                temp_output_path=str(self.encoded_file),  # Update with .mkv path
                last_error_message="MP4 failed, retrying MKV.",
            )

            logger.info(f"Retrying with MKV: {self.encoded_file.name}")
            res_mkv = run_cmd(  # Pass list
                self.encode_cmd_list,
                src_file_for_log=self.original_media_file.path,
                error_log_dir_for_run_cmd=self.error_dir_base,
                show_cmd=show_cmd_output,
                cmd_log_file_path=cmd_log_file_path_val,
            )

            if res_mkv and res_mkv.returncode == 0:
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
                    if (
                        not self._check_and_handle_oversized()
                    ):  # Check oversized for MKV too
                        pass
                else:
                    error_msg_mkv = f"MKV ffmpeg reported success but output file {self.encoded_file.name} is missing."
                    logger.error(error_msg_mkv)
                    self.failed_action(
                        res_mkv.stdout,
                        f"{res_mkv.stderr}\n{error_msg_mkv}",
                        res_mkv.returncode,
                        is_retryable_error=True,
                    )
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
                    is_retryable_error=True,
                )
                raise MP4MKVEncodeFailException(
                    f"Both MP4 and MKV encoding failed for {self.original_media_file.path.name}"
                )

        else:  # This means either MP4 encoding failed and it was an oversized_retry, or MKV encoding failed (which is implicitly not an oversized_retry context for the MP4->MKV switch logic)
            error_details = f"ffmpeg command failed for {self.encoded_file.name}. RC: {res.returncode if res else 'N/A'}"
            if is_oversized_retry:  # If this was a re-try due to oversized, this is a hard failure for this CRF attempt
                error_details += " (during oversized re-encode attempt)"
            logger.error(error_details)

            if self.encoded_file.exists():
                self.encoded_file.unlink(missing_ok=True)

            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True,  # Default to retryable unless it's an oversized check that then fails ffmpeg
            )
            raise EncodingException(error_details)

    def _build_ffmpeg_stream_maps(self):
        _video_map_cmd = ""
        max_fps = 240

        if not self.pre_encoder or not hasattr(
            self.pre_encoder, "output_video_streams"
        ):
            logger.error(
                "PreVideoEncoder instance or its stream data not available for building maps."
            )
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
                    if fps_fraction.denominator == 0:
                        raise ValueError("Denominator is zero")
                    if fps_fraction > 0 and fps_fraction <= max_fps:
                        if fps_fraction.denominator == 1:
                            fps_str = str(fps_fraction.numerator)
                        else:
                            fps_val = float(fps_fraction)
                            fps_str = (
                                f"{fps_val:.3f}".rstrip("0").rstrip(".")
                                if fps_val % 1 != 0
                                else str(int(fps_val))
                            )
                except (ZeroDivisionError, ValueError) as e:
                    logger.warning(
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
            logger.debug(
                f"No output audio streams selected for {self.original_media_file.filename}. Output will have no audio."
            )
        for audio_stream in audio_streams_to_map:
            stream_index = int(audio_stream.get("index", 0))
            channels = int(audio_stream.get("channels", 2))
            is_mkv_target_for_audio_check = (
                self.encoded_file.suffix.lower() == ".mkv"
            )  # Check current target
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
                    except (ValueError, TypeError):
                        pass
                elif "tags" in audio_stream and "BPS-eng" in audio_stream["tags"]:
                    try:
                        original_bitrate = int(audio_stream["tags"]["BPS-eng"])
                    except (ValueError, TypeError):
                        pass

                target_opus_bitrate = max_opus_bitrate // 2
                if original_bitrate > 0:
                    target_opus_bitrate = min(original_bitrate, max_opus_bitrate)

                abitrate_cmd = f"-b:a:{audio_output_idx} {target_opus_bitrate} "
                if (
                    not is_mkv_target_for_audio_check
                ):  # If current target is MP4 and we need Opus
                    logger.info(
                        f"Audio stream {stream_index} ({input_codec_name}) requires re-encode to Opus for MP4 target. "
                        f"The output container will be switched to MKV to accommodate Opus audio. "
                        f"Original MP4 target: {self.encoded_file.name}."
                    )
                    # This flags that MKV is needed. The actual switch happens in _ffmpeg_encode_video if MP4 fails.
                    # Or, if we want to be proactive, we could change self.encoded_file here,
                    # but that might complicate the retry logic if other aspects of MP4 would have worked.
                    # For now, this log is informational; the MP4->MKV switch is reactive.
                    # Let's assume the existing logic handles the switch if Opus is forced.
                    # The key is that if Opus IS used, the container BETTER be MKV.
                    # If an MP4 attempt is made with Opus, it should fail, then MKV retry should use Opus.
                    # The current code in _ffmpeg_encode_video seems to switch to MKV if MP4 fails,
                    # then re-calls _build_ffmpeg_stream_maps. At that point, is_mkv_target_for_audio_check will be true.
                    pass  # No change to scodec_name here, it's already Opus.

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
            stream_index = int(subtitle_stream.get("index", 0))
            # Default for MP4: attempt conversion to mov_text. This will fail for bitmap like PGS.
            scodec_name_default_for_mp4_conversion = "mov_text"
            scodec_name = scodec_name_default_for_mp4_conversion

            input_subtitle_codec = subtitle_stream.get("codec_name", "").lower()
            is_mkv_target = self.encoded_file.suffix.lower() == ".mkv"

            if is_mkv_target:
                # For MKV, try to copy if it's a known compatible codec from our config list.
                if input_subtitle_codec in SUBTITLE_MKV_CODECS:
                    scodec_name = "copy"
                else:
                    # If not in the explicit list, still default to 'copy' for MKV, as it's often robust.
                    logger.warning(
                        f"MKV Target: Subtitle codec '{input_subtitle_codec}' for stream {stream_index} "
                        f"not in SUBTITLE_MKV_CODECS. Defaulting to 'copy'. This might work or fail."
                    )
                    scodec_name = "copy"
            else:  # MP4 Target
                # If input is already MP4-native text subtitle, copy it.
                if input_subtitle_codec in ["mov_text", "tx3g"]:
                    scodec_name = "copy"
                # If input is a common text-based format (SRT, ASS, WebVTT), ffmpeg can often convert these to mov_text.
                elif input_subtitle_codec in ["subrip", "srt", "ass", "ssa", "webvtt"]:
                    scodec_name = scodec_name_default_for_mp4_conversion  # Explicitly set to convert
                    logger.debug(
                        f"MP4 Target: Text-based subtitle stream {stream_index} (codec {input_subtitle_codec}) "
                        f"will be converted to '{scodec_name}'."
                    )
                # For bitmap subtitles (like PGS, VobSub), conversion to mov_text by ffmpeg will fail.
                # The current behavior is to let this attempt happen (scodec_name remains 'mov_text').
                # If it fails, the overall encoding falls back to MKV.
                # In the MKV retry, the logic above for `is_mkv_target == True` will apply, leading to `copy`.
                # This matches the desired flow: try MP4 (it will fail for PGS), then retry MKV (PGS will be copied).
                elif input_subtitle_codec in [
                    "pgs",
                    "hdmv_pgs_subtitle",
                    "vobsub",
                    "dvd_subtitle",
                ]:
                    logger.debug(
                        f"MP4 Target: Bitmap subtitle stream {stream_index} (codec {input_subtitle_codec}) "
                        f"will attempt conversion to '{scodec_name}'. This is expected to fail and trigger MKV retry."
                    )
                else:  # Unknown subtitle codec for MP4
                    logger.warning(
                        f"MP4 Target: Unknown subtitle codec '{input_subtitle_codec}' for stream {stream_index}. "
                        f"Attempting conversion to '{scodec_name}'. This may fail."
                    )

            _subtitle_map_cmd += (
                f"-map 0:{stream_index} -c:s:{subtitle_output_idx} {scodec_name} "
            )
            subtitle_output_idx += 1
        self.subtitle_map_cmd_part = _subtitle_map_cmd.strip()

    def _build_encode_command(self):
        input_path_str = str(self.original_media_file.path.resolve())
        output_path_str = str(self.encoded_file.resolve())
        logger.debug(
            f"Building encode command. Input path: '{input_path_str}', Output path: '{output_path_str}'"
        )

        cmd_list = ["ffmpeg", "-y", "-i", input_path_str]
        cmd_list.extend(["-c:v", self.encoder_codec_name, "-crf", str(self.crf)])

        if self.video_map_cmd_part:  # Example: "-map 0:0 -r 23.976"
            cmd_list.extend(shlex.split(self.video_map_cmd_part))

        cmd_list.extend(["-metadata", f"comment={self.encoded_comment_text}"])

        if self.audio_map_cmd_part:  # Example: "-map 0:1 -c:a:0 copy"
            cmd_list.extend(shlex.split(self.audio_map_cmd_part))

        if self.subtitle_map_cmd_part:  # Example: "-map 0:2 -c:s:0 mov_text"
            cmd_list.extend(shlex.split(self.subtitle_map_cmd_part))

        cmd_list.append(output_path_str)

        self.encode_cmd_list = cmd_list  # Store as list

        try:
            display_cmd = (
                subprocess.list2cmdline(cmd_list)
                if os.name == "nt"
                else " ".join(shlex.quote(s) for s in cmd_list)
            )
        except AttributeError:  # Python < 3.8
            display_cmd = " ".join(shlex.quote(s) for s in cmd_list)
        logger.debug(
            f"Built ffmpeg command list for {self.original_media_file.filename}:\n{cmd_list}\nFormatted for display: {display_cmd}"
        )

    def write_success_log(
        self, log_date_in_filename=True, update_dic: Optional[dict] = None
    ):
        video_specific_log_data = {
            "pre_encode_info": {
                "is_manual_mode": self.pre_encoder.is_manual_mode
                if self.pre_encoder
                else (
                    self.encode_info.pre_encoder_data.get("is_manual_mode")
                    if self.encode_info.pre_encoder_data
                    else "N/A"
                ),
                "estimated_size_ratio_percent": (
                    float(self.pre_encoder.best_ratio * 100)
                    if self.pre_encoder and self.pre_encoder.best_ratio is not None
                    else (
                        float(self.encode_info.pre_encoder_data["best_ratio"] * 100)
                        if self.encode_info.pre_encoder_data
                        and self.encode_info.pre_encoder_data.get("best_ratio")
                        is not None
                        else None
                    )
                ),
                "crf_checking_time_formatted": format_timedelta(
                    self.pre_encoder.crf_checking_time
                )
                if self.pre_encoder and self.pre_encoder.crf_checking_time
                else (
                    format_timedelta(
                        timedelta(
                            seconds=self.encode_info.pre_encoder_data[
                                "crf_checking_time_seconds"
                            ]
                        )
                    )
                    if self.encode_info.pre_encoder_data
                    and self.encode_info.pre_encoder_data.get(
                        "crf_checking_time_seconds"
                    )
                    is not None
                    else "0s"
                ),
                "target_vmaf_for_pre_encode": TARGET_VMAF,
            },
        }
        if update_dic:
            video_specific_log_data.update(update_dic)

        super().write_success_log(
            log_date_in_filename=log_date_in_filename,
            update_dic=video_specific_log_data,
        )