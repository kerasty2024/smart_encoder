"""
This module defines the AudioEncoder service for the Smart Encoder application.

It provides a concrete implementation of the base `Encoder` class, specialized for
handling the encoding of audio files. Its primary use case is to convert various
source audio formats into a standardized, efficient format like Opus, which is
well-suited for mobile devices.
"""

import os
import shlex
import subprocess
from datetime import datetime
from typing import Any, Optional

import yaml
from loguru import logger

from ..config.audio import (
    AUDIO_COMMENT_ENCODED,
    AUDIO_ENCODED_RAW_DIR,
    AUDIO_ENCODED_ROOT_DIR,
    DEFAULT_AUDIO_ENCODER,
    TARGET_BIT_RATE_IPHONE_XR,
)
from ..config.common import (
    COMMAND_TEXT,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_PENDING,
)
from ..domain.exceptions import EncodingException
from ..domain.media import MediaFile
from ..domain.temp_models import EncodeInfo
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import formatted_size
from .encoder_base import Encoder


class AudioEncoder(Encoder):
    """
    Handles the end-to-end process of encoding a single audio file.

    This class is specifically designed for audio-only encoding tasks, typically
    used by the `PhoneEncodingPipeline` when the `--audio-only` flag is active.
    It takes a source audio file and converts it to a target format (e.g., Opus)
    with a specified bitrate.

    Responsibilities:
    - Setting up audio-specific encoding parameters (codec, bitrate).
    - Generating the correct FFmpeg command for audio conversion.
    - Executing the FFmpeg command and monitoring its outcome.
    - Embedding detailed metadata into the encoded audio file.
    - Managing the job's state using an `EncodeInfo` object.
    - Handling success (logging, file management) and failure (error logging).

    Attributes:
        encoder_codec_name (str): The name of the audio codec to use (e.g., 'libopus').
        target_bit_rate (int): The target audio bitrate in bits per second.
        encoded_dir (Path): The directory where the encoded audio file will be saved.
        encoded_file (Path): The full path to the final output audio file.
        encode_info (EncodeInfo): The state manager for this encoding job.
    """

    def __init__(
        self,
        media_file: MediaFile,
        target_bit_rate: int = TARGET_BIT_RATE_IPHONE_XR,
        args: Optional[Any] = None,
    ):
        """
        Initializes the AudioEncoder for a specific media file.

        This sets up all the necessary paths, encoding parameters, and the state
        management object (`EncodeInfo`) required for the audio encoding task.

        Args:
            media_file: The `MediaFile` object representing the source audio file.
            target_bit_rate: The desired bitrate for the output audio in bits per second.
            args: The command-line arguments, used for flags like `--move-raw-file`.
        """
        super().__init__(media_file=media_file, args=args)
        self.encoder_codec_name = DEFAULT_AUDIO_ENCODER
        self.target_bit_rate = target_bit_rate
        self.encoded_dir = (
            AUDIO_ENCODED_ROOT_DIR / self.original_media_file.relative_dir
        ).resolve()
        self.encoded_file = (
            self.encoded_dir / f"{self.original_media_file.stem}{self._get_file_extension()}"
        )
        self.success_log_output_dir = self.encoded_dir
        self.encoded_raw_files_target_dir = (
            AUDIO_ENCODED_RAW_DIR / self.original_media_file.relative_dir
        ).resolve()
        encode_info_storage_dir = self.encoded_dir / ".encode_info_cache"
        encode_info_storage_dir.mkdir(parents=True, exist_ok=True)
        self.encode_info = EncodeInfo(
            media_file.md5, storage_dir=encode_info_storage_dir
        )
        if not self.encode_info.load():
            self.encode_info.dump(
                status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path)
            )

    def _get_file_extension(self) -> str:
        """
        Determines the appropriate file extension based on the audio encoder being used.

        For example, if the encoder is 'libopus', the extension will be '.opus'.

        Returns:
            A string representing the file extension (e.g., ".opus").
        """
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
        """
        Creates a YAML-formatted string containing metadata about the encoding process.

        This metadata is embedded in the output file's 'comment' tag. It includes
        information about the source file, the encoder used, and a tag to identify
        that the file has been processed by this application. This helps prevent
        re-encoding the same file in the future.
        """
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
        """
        Orchestrates the actual audio encoding process.

        This method implements the abstract `encode` method from the `Encoder`
        base class. It performs the following steps:
        1. Generates the metadata comment.
        2. Ensures the output directory exists.
        3. Constructs the full FFmpeg command as a list of arguments.
        4. Updates the job's state to `encoding_ffmpeg_started` in `EncodeInfo`.
        5. Executes the FFmpeg command using the `run_cmd` utility.
        6. Handles the result:
           - On success, it sets `no_error` to True and verifies the output file.
           - On failure, it calls `failed_action` to log the error and update the
             job state, then raises an `EncodingException`.
        """
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        cmd_list = ["ffmpeg", "-y", "-i", str(self.original_media_file.path.resolve())]
        cmd_list.extend(["-vn", "-map", "0:a"])
        cmd_list.extend(
            ["-c:a", self.encoder_codec_name, "-b:a", str(self.target_bit_rate)]
        )
        cmd_list.extend(["-metadata", f"comment={self.encoded_comment_text}"])
        cmd_list.append(str(self.encoded_file.resolve()))

        self.encode_cmd_list = cmd_list  # Store as list
        logger.debug(f"AudioEncoder command list: {self.encode_cmd_list}")

        try:
            cmd_str_for_info = (
                subprocess.list2cmdline(cmd_list)
                if os.name == "nt"
                else " ".join(shlex.quote(s) for s in cmd_list)
            )
        except Exception:
            cmd_str_for_info = " ".join(map(str, cmd_list))

        self.encode_info.dump(
            status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
            ffmpeg_command=cmd_str_for_info,
            temp_output_path=str(self.encoded_file),
            encoder=self.encoder_codec_name,
        )

        show_cmd_output = __debug__
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            self.encode_cmd_list,  # Pass list
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