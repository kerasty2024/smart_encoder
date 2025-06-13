"""
This module defines the PhoneVideoEncoder, a specialized service for encoding
video files with settings optimized for mobile devices, particularly iPhones.
"""

import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

# Import configuration constants specific to the phone profile.
from ..config.common import (
    COMMAND_TEXT,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_PENDING,
)
from ..config.video import (
    AUDIO_CODEC_IPHONE_XR,
    COMPLETED_RAW_DIR,
    IPHONE_XR_OPTIONS,
    MANUAL_AUDIO_BIT_RATE_IPHONE_XR,
    MANUAL_VIDEO_BIT_RATE_IPHONE_XR,
    OUTPUT_DIR_IPHONE,
    VIDEO_CODEC_IPHONE_XR,
    VIDEO_COMMENT_ENCODED,
)

# Import domain models and base classes.
from ..domain.exceptions import EncodingException
from ..domain.media import MediaFile
from ..domain.temp_models import EncodeInfo
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import formatted_size
from .encoder_base import Encoder


class PhoneVideoEncoder(Encoder):
    """
    A specialized encoder for creating videos optimized for phone playback.

    This class inherits from the base `Encoder` but uses a fixed set of encoding
    parameters (bitrate, resolution, frame rate) defined in the configuration.
    This makes it a fast and consistent way to prepare videos for mobile devices,
    as it bypasses the time-consuming quality analysis (CRF search) performed
    by the standard `VideoEncoder`.
    """

    def __init__(self, media_file: MediaFile, args: Any):
        """
        Initializes the PhoneVideoEncoder instance.

        It sets up paths and parameters specific to the phone encoding profile,
        such as the output directory, codecs, and target bitrates, and initializes
        the job state manager (`EncodeInfo`).

        Args:
            media_file: The `MediaFile` object representing the source video.
            args: The command-line arguments passed to the application.
        """
        # Call the parent constructor to set up common attributes.
        super().__init__(media_file, args)

        # --- Configure phone-specific properties ---
        # The output directory for phone-encoded files.
        self.encoded_dir = Path(OUTPUT_DIR_IPHONE).resolve()
        # The video and audio codecs to use, from config.
        self.encoder_codec_name = VIDEO_CODEC_IPHONE_XR
        self.audio_encoder_codec_name = AUDIO_CODEC_IPHONE_XR
        # Additional FFmpeg command-line options (e.g., for scaling and frame rate).
        self.cmd_options_phone = IPHONE_XR_OPTIONS
        # The success log for this type of encode will be in the project's root.
        self.success_log_output_dir = Path.cwd()
        # The final output file path and name.
        self.encoded_file = self.encoded_dir / f"{self.original_media_file.stem}.mp4"
        # The directory to move the original file to after successful encoding.
        self.encoded_raw_files_target_dir = (
            COMPLETED_RAW_DIR
            / "phone_encoded_raw"
            / self.original_media_file.relative_dir
        ).resolve()

        # Set up a dedicated directory for job progress files (`.encode_info_cache`).
        encode_info_storage_dir = self.encoded_dir / ".encode_info_cache"
        encode_info_storage_dir.mkdir(parents=True, exist_ok=True)

        # Initialize or load the job state from the progress file.
        self.encode_info = EncodeInfo(
            media_file.md5, storage_dir=encode_info_storage_dir
        )
        if not self.encode_info.load():
            self.encode_info.dump(
                status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path)
            )

    def _set_metadata_comment(self):
        """
        Generates a detailed YAML-formatted comment string to be embedded in the
        encoded file's metadata, specific to the phone encoding profile.

        This metadata helps identify files processed by this tool, preventing
        re-encoding and providing useful information about the encoding process.
        """
        # 1. Create a dictionary containing all the metadata to be stored.
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
        # 2. Convert the dictionary to a compact, single-line YAML string.
        #    This string will be embedded in the 'comment' metadata tag of the output file.
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
        Executes the video encoding process using the fixed phone profile.

        This method builds and runs the FFmpeg command with the hardcoded settings
        (bitrate, codecs, filters) defined in the application's configuration,
        making it suitable for producing files for mobile devices.
        """
        # 1. Generate the metadata comment to embed in the output file.
        self._set_metadata_comment()
        self.encoded_dir.mkdir(parents=True, exist_ok=True)

        # 2. Prepare the video and audio bitrate strings from the configuration constants.
        video_bitrate_str = f"{MANUAL_VIDEO_BIT_RATE_IPHONE_XR}k"
        audio_bitrate_str = str(MANUAL_AUDIO_BIT_RATE_IPHONE_XR)

        # 3. Build the FFmpeg command as a list of arguments for safe execution.
        cmd_list = ["ffmpeg", "-y", "-i", str(self.original_media_file.path.resolve())]
        # The phone options string (e.g., "-vf scale=-1:414 -r 20") is parsed into a list of arguments.
        cmd_list.extend(shlex.split(self.cmd_options_phone))
        # Add video codec and bitrate settings.
        cmd_list.extend(["-c:v", self.encoder_codec_name, "-b:v", video_bitrate_str])
        # Add audio codec and bitrate settings.
        cmd_list.extend(
            ["-c:a", self.audio_encoder_codec_name, "-b:a", audio_bitrate_str]
        )
        # Add the generated metadata comment and the final output file path.
        cmd_list.extend(["-metadata", f"comment={self.encoded_comment_text}"])
        cmd_list.append(str(self.encoded_file.resolve()))

        self.encode_cmd_list = cmd_list
        logger.debug(f"PhoneVideoEncoder command list: {self.encode_cmd_list}")

        # For logging purposes, create a single, display-friendly command string.
        try:
            cmd_str_for_info = (
                subprocess.list2cmdline(cmd_list)
                if os.name == "nt"
                else " ".join(shlex.quote(s) for s in cmd_list)
            )
        except Exception:
            cmd_str_for_info = " ".join(map(str, cmd_list))

        # 4. Update the job status to 'encoding_ffmpeg_started' before running the command.
        #    This ensures that if the process is interrupted, it can be resumed correctly.
        self.encode_info.dump(
            status=JOB_STATUS_ENCODING_FFMPEG_STARTED,
            ffmpeg_command=cmd_str_for_info,
            temp_output_path=str(self.encoded_file),
            encoder=self.encoder_codec_name,
        )

        # 5. Execute the FFmpeg command using the utility function.
        show_cmd_output = __debug__  # Show command output only in debug mode.
        cmd_log_file_path_val = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            self.encode_cmd_list,
            src_file_for_log=self.original_media_file.path,
            error_log_dir_for_run_cmd=self.error_dir_base,
            show_cmd=show_cmd_output,
            cmd_log_file_path=cmd_log_file_path_val,
        )

        # 6. Process the result of the command execution.
        if res and res.returncode == 0:
            # FFmpeg command was successful.
            self.no_error = True
            if self.encoded_file.exists():
                # If configured, set the modification time of the new file to match the original.
                if self.keep_mtime:
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
                            f"Could not set modification time for {self.encoded_file.name}: {utime_err}"
                        )
                self.encoded_size = self.encoded_file.stat().st_size
            else:
                # This is an unexpected but critical error state.
                error_msg = f"Phone encode: FFmpeg reported success, but the output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(
                    res.stdout,
                    f"{res.stderr}\n{error_msg}",
                    res.returncode,
                    is_retryable_error=True,
                )
                raise EncodingException(error_msg)
        else:
            # FFmpeg command failed.
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True,
            )
            raise EncodingException(
                f"PhoneVideoEncoding failed for {self.original_media_file.filename}"
            )