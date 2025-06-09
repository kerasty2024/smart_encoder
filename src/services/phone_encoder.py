import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import yaml
from loguru import logger

from ..config.common import (
    BASE_ERROR_DIR,
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
from ..domain.exceptions import EncodingException
from ..domain.media import MediaFile
from ..domain.temp_models import EncodeInfo
from ..utils.ffmpeg_utils import run_cmd
from ..utils.format_utils import formatted_size
from .encoder_base import Encoder


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
        self.encode_info = EncodeInfo(
            media_file.md5, storage_dir=encode_info_storage_dir
        )
        if not self.encode_info.load():
            self.encode_info.dump(
                status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path)
            )

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
        video_bitrate_str = f"{MANUAL_VIDEO_BIT_RATE_IPHONE_XR}k"
        audio_bitrate_str = str(MANUAL_AUDIO_BIT_RATE_IPHONE_XR)

        cmd_list = ["ffmpeg", "-y", "-i", str(self.original_media_file.path.resolve())]
        # self.cmd_options_phone is a string like " -vf scale=-1:414 -r 20 ", split it
        cmd_list.extend(shlex.split(self.cmd_options_phone))
        cmd_list.extend(["-c:v", self.encoder_codec_name, "-b:v", video_bitrate_str])
        cmd_list.extend(
            ["-c:a", self.audio_encoder_codec_name, "-b:a", audio_bitrate_str]
        )
        cmd_list.extend(["-metadata", f"comment={self.encoded_comment_text}"])
        cmd_list.append(str(self.encoded_file.resolve()))

        self.encode_cmd_list = cmd_list  # Store as list
        logger.debug(f"PhoneVideoEncoder command list: {self.encode_cmd_list}")

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
                        os.utime(
                            self.encoded_file,
                            (
                                datetime.now().timestamp(),
                                self.original_media_file.path.stat().st_mtime,
                            ),
                        )
                    except Exception as utime_err:
                        logger.warning(
                            f"Could not set mtime for phone encode {self.encoded_file.name}: {utime_err}"
                        )
                self.encoded_size = self.encoded_file.stat().st_size
            else:
                error_msg = f"Phone encode: ffmpeg reported success but output file {self.encoded_file.name} is missing."
                logger.error(error_msg)
                self.failed_action(
                    res.stdout,
                    f"{res.stderr}\n{error_msg}",
                    res.returncode,
                    is_retryable_error=True,
                )
                raise EncodingException(error_msg)
        else:
            self.failed_action(
                res.stdout if res else "",
                res.stderr if res else "",
                res.returncode if res else -1,
                is_retryable_error=True,
            )
            raise EncodingException(
                f"PhoneVideoEncoding failed for {self.original_media_file.filename}"
            )