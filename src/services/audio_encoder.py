import os
import shlex
import subprocess
from datetime import datetime
from pathlib import Path
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
    BASE_ERROR_DIR,
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
        self.encode_info = EncodeInfo(
            media_file.md5, storage_dir=encode_info_storage_dir
        )
        if not self.encode_info.load():
            self.encode_info.dump(
                status=JOB_STATUS_PENDING, ori_video_path=str(media_file.path)
            )

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