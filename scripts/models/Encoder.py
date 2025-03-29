import os
import platform
import shutil
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from pprint import pformat

import yaml
from loguru import logger

from scripts.controllers.functions import format_timedelta, formatted_size, run_cmd
from scripts.models.Log import ErrorLog, SuccessLog
from scripts.models.MediaFile import MediaFile
from scripts.models.PreEncoder import PreVideoEncoder, PreEncoder
from scripts.models.PreVideoEncodeExceptions import SkippedVideoFileException
from scripts.models.VideoEncodeExceptions import MP4MKVEncodeFailException
from scripts.settings.audio import (
    DEFAULT_AUDIO_ENCODER,
    TARGET_BIT_RATE_IPHONE_XR,
    AUDIO_ENCODED_ROOT_DIR,
    AUDIO_ENCODED_RAW_DIR,
    AUDIO_COMMENT_ENCODED,
)
from scripts.settings.common import COMMAND_TEXT, BASE_ERROR_DIR
from scripts.settings.video import (
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
    ENCODERS,
    MANUAL_CRF_INCREMENT_PERCENT,
)


class Encoder:
    """
    Base class for encoding media files.

    Attributes:
        pre_encoder: Instance of PreEncoder for pre-encoding tasks.
        encode_start_datetime: Start time of the encoding process.
        encode_end_datetime: End time of the encoding process.
        encode_time: Total duration of encoding.
        total_time: total duration of encoding (including pre-encode)
        encoder: Name of the encoder used.
        crf: Constant Rate Factor for encoding.
        encoded_dir: Directory where encoded files are stored.
        encoded_root_dir: Root directory for encoded files.
        encoded_file: Path to the encoded file.
        encoded_size: Size of the encoded file in bytes.
        renamed_original_file: Path of the renamed original file, if applicable.
        success_log: SuccessLog instance for logging successful encodings.
    """

    pre_encoder: PreEncoder
    encode_start_datetime: datetime
    encode_end_datetime: datetime
    encode_time: timedelta
    total_time: timedelta
    encoder: str
    crf: int
    encoded_dir: Path
    encoded_root_dir: Path
    encoded_file: Path
    encoded_size: int
    renamed_original_file: Path
    success_log: SuccessLog

    def __init__(self, media_file: MediaFile, args):
        """
        Initializes the Encoder with a media file and encoding arguments.

        :param media_file: Media file to be encoded.
        :param args: Additional encoding arguments.
        """
        self.original_media_file = media_file
        self.no_error = True
        self.error_dir = Path(BASE_ERROR_DIR).resolve()
        self.error_output_file = Path()
        self.error_log_file = Path()
        self.success_log_dir = Path.cwd()
        self.encoded_comment = ""
        self.encode_cmd = ""
        self.encoded_raw_dir = Path()
        self.keep_mtime = args.keep_mtime
        self.args = args

    def start(self):
        """
        Starts the encoding process, including setup and invoking the encode method.
        """

        def _encode():
            self.encode_start_datetime = datetime.now()
            logger.info(
                f"Encoding: {self.original_media_file.path.relative_to(Path.cwd())}"
            )
            self.encode()
            self.encode_end_datetime = datetime.now()
            self.encode_time = self.encode_end_datetime - self.encode_start_datetime
            self.total_time = (getattr(self.pre_encoder, "crf_checking_time", 0) # self.pre_encoder.crf_checking_time が存在する場合に total_time を計算
              if hasattr(self, "pre_encoder") else 0) + self.encode_time

            self.write_success_log()

        logger.info(
            f"Starting: {self.original_media_file.path.relative_to(Path.cwd())}"
        )
        self.encoded_dir.mkdir(parents=True, exist_ok=True)
        logger.debug(pformat(vars(self)))
        if 'pre_encoder' in vars(self):
            try:
                self.pre_encoder.start()
            except SkippedVideoFileException:  # no need to encode

                return
        _encode()
        self.post_actions()

    def failed_action(self, res):
        """
        Handles actions when encoding fails. Intended for subclass customization.

        :param res: Result of the failed encoding command.
        """
        pass

    def write_success_log(self, log_date=True, update_dic: dict = None):
        """
        Logs details of a successful encoding.

        :param log_date: If True, include the date in the log.
        :param update_dic: Additional details to include in the log.
        """
        if not self.no_error or (self.pre_encoder and self.pre_encoder.renamed_file):
            return

        log_dict = {
            "index": 0,
            "input file": str(self.original_media_file.path),
            "source file md5": self.original_media_file.md5,
            "source file sha256": self.original_media_file.sha256,
            "encoder": getattr(self, "encoder", "N/A"),
            "file duration(s)": self.original_media_file.duration,
            "file duration": format_timedelta(
                timedelta(seconds=self.original_media_file.duration)
            ),
            "elapsed time": format_timedelta(self.total_time),
            "encode time efficiency (elapsed_min/Video_min)": round(
                self.encode_time.total_seconds() / self.original_media_file.duration, 2
            ),
            "encoded ratio": round(
                self.encoded_size / self.original_media_file.size, 2
            ),
            "ended time": self.encode_end_datetime.strftime("%Y%m%d_%H:%M:%S"),
            "encoded file": str(self.encoded_file),
            "processor": platform.processor(),
            "platform": platform.platform(),
        }

        if update_dic:
            log_dict.update(update_dic)

        self.success_log = SuccessLog(self.success_log_dir, log_date=log_date)
        self.success_log.write(log_dict)

    def encode(self):
        """
        Abstract method for encoding. Must be implemented in subclasses.
        """
        raise NotImplementedError("Subclasses must implement the encode method.")

    def move_raw_file(self):
        """
        Moves the original media file to the encoded raw directory if specified.
        """
        self.encoded_raw_dir.mkdir(parents=True, exist_ok=True)
        raw_file_path_target = self.encoded_raw_dir / self.original_media_file.filename

        if not raw_file_path_target.exists():
            try:
                shutil.move(self.original_media_file.path, self.encoded_raw_dir)
            except shutil.Error as e:
                logger.debug(e)
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

    def post_actions(self):
        """
        Performs final actions after encoding, such as moving the raw file if required.
        """
        if self.pre_encoder and self.pre_encoder.renamed_file:
            return

        if (
                not self.error_output_file
                and not self.renamed_original_file
                and self.args.move_raw_file
        ):
            self.move_raw_file()

        logger.success(
            f"Completed: {self.original_media_file.path.relative_to(Path.cwd())}, "
            f"total time: {format_timedelta(self.encode_time)}, "
            f"{formatted_size(self.original_media_file.size)} -> {formatted_size(self.encoded_file.stat().st_size)} "
            f"({int((self.encoded_file.stat().st_size / self.original_media_file.size) * 100)}%)"
        )

    def set_encoded_comment(self):
        """
        Sets the metadata comment for the encoded file. To be implemented in subclasses.
        """
        raise NotImplementedError(
            "Subclasses must implement the set_encoded_comment method."
        )


class VideoEncoder(Encoder):
    """
    Encodes video files with specific parameters and handling for post-encoding tasks.

    Inherits from Encoder and provides additional functionality for video encoding,
    including handling video, audio, and subtitle streams, and managing post-encoding actions.
    """

    def __init__(self, media_file: MediaFile, args):
        """
        Initializes the VideoEncoder with paths and settings for video encoding.

        :param media_file: The media file to encode.
        :param args: Additional arguments for encoding.
        """
        super().__init__(media_file, args)
        self.encoded_dir: Path = (
                VIDEO_OUT_DIR_ROOT
                / self.original_media_file.path.parent.absolute().relative_to(
            Path.cwd().absolute()
        )
        )
        self.encoded_file = (
                self.encoded_dir / f"{self.original_media_file.path.stem}.mp4"
        )
        self.encoded_raw_dir = (
                COMPLETED_RAW_DIR
                / self.original_media_file.path.parent.absolute().relative_to(
            Path.cwd().absolute()
        )
        )
        self.encoded_root_dir = VIDEO_OUT_DIR_ROOT
        self.error_dir = BASE_ERROR_DIR
        self.pre_encoder = PreVideoEncoder(media_file, self.args.manual_mode)

        self.video_map_cmd = ""
        self.audio_map_cmd = ""
        self.subtitle_map_cmd = ""

    def encode(self):
        """
        Initiates the encoding process.
        sets encoding parameters and starts the ffmpeg encoding process.
        """
        self.encoder = self.pre_encoder.best_encoder
        self.crf = self.pre_encoder.best_crf
        try:
            self.ffmpeg_encode()
        except MP4MKVEncodeFailException:
            self.pre_encoder.move_error_file('MP4_or_MKV_Encode_Failed')

    def over_sized_actions(self):
        """
        Handles cases where the encoded file is larger than the original.
        Deletes the oversized file, increases CRF, and retries encoding.
        """
        if self.encoded_size > self.original_media_file.size:
            self.encoded_file.unlink(missing_ok=True)
            logger.debug(
                f"File is oversized: {self.original_media_file.path}, "
                f"ratio: {self.encoded_size / self.original_media_file.size}, "
                f"CRF: {self.crf}"
            )
            self.crf += int(self.crf * MANUAL_CRF_INCREMENT_PERCENT / 100)
            self.pre_encoder.encode_info.dump(
                crf=self.crf,
                encoder=self.encoder,
                ori_video_path=self.original_media_file.path.as_posix(),
            )
            return self.ffmpeg_encode(update_dict={"manual crf": True})
        else:
            self.pre_encoder.encode_info.remove_file()

    def set_encoded_comment(self, update_dic: dict = None):
        """
        Sets the metadata comment for the encoded file.

        :param update_dic: Additional metadata to include in the comment.
        """
        comment_dic = {
            "comment": VIDEO_COMMENT_ENCODED,
            "encoders": ENCODERS,
            "CRF": self.crf,
            "source file": self.original_media_file.path.name,
            "source file size": formatted_size(self.original_media_file.size),
            "source file md5": self.original_media_file.md5,
            "source file sha256": self.original_media_file.sha256,
            "manual crf": self.pre_encoder.manual_mode,
        }
        if update_dic:
            comment_dic.update(update_dic)

        self.encoded_comment = yaml.dump(
            comment_dic,
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=99999,
        ).strip()

    def ffmpeg_encode(self, update_dict: dict = None):
        """
        Executes the ffmpeg command to encode the video.
        Handles errors and retries with alternative settings if necessary.

        :param update_dict: Additional parameters for the encoding command.
        """

        def success_action():
            """Handles actions upon successful encoding, such as updating file size and moving raw files."""
            self.no_error = True
            self.encoded_size = self.encoded_file.stat().st_size
            if self.keep_mtime:
                os.utime(
                    self.encoded_file,
                    times=(
                        datetime.now().timestamp(),
                        self.original_media_file.path.stat().st_mtime,
                    ),
                )
            self.over_sized_actions()
            self.move_raw_file()

        self.set_video_map_cmd()
        self.set_audio_map_cmd()
        self.set_subtitle_map_cmd()
        self.set_encoded_comment(update_dict)
        self.set_encode_cmd()

        show_cmd = __debug__

        cmd_path = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            self.encode_cmd,
            self.original_media_file.path,
            self.error_dir,
            show_cmd=show_cmd,
            cmd_path=cmd_path,
        )

        if res.returncode == 0:
            success_action()
        elif self.encoded_file.suffix == ".mp4":
            logger.warning(
                f"MP4 encoding failed for {self.original_media_file.path}. "
                f"Return code: ({res.returncode}):{os.linesep}{res}"
            )
            self.encoded_file.unlink(missing_ok=True)
            self.encoded_file = self.encoded_file.with_suffix(".mkv")
            self.set_encode_cmd()
            res = run_cmd(
                self.encode_cmd,
                self.original_media_file.path,
                self.error_dir,
                show_cmd=show_cmd,
                cmd_path=cmd_path,
            )
            if res.returncode == 0:
                success_action()
            else:
                self.encoded_file.unlink(missing_ok=True)
                raise MP4MKVEncodeFailException(f"MP4 encoding failed for {self.original_media_file.path}. ")

    def set_video_map_cmd(self):
        """
        Configures the video stream mapping command for ffmpeg based on pre-encoded video streams.
        """
        _video_map_cmd = ""
        max_fps = 240
        for video_stream in self.pre_encoder.output_video_streams:
            fps = "24"
            if "avg_frame_rate" in video_stream:
                try:
                    fps_fraction = Fraction(video_stream.get("avg_frame_rate"))
                    if fps_fraction <= max_fps:
                        fps = fps_fraction
                except (ZeroDivisionError, Exception) as e:
                    logger.error(e)
                    self.pre_encoder.output_video_streams.remove(video_stream)
                    logger.error("Removed faulty video stream.")
                    break
            else:
                logger.warning(
                    f"avg_frame_rate not found in {self.original_media_file.path}"
                )
            _video_map_cmd += f'-map 0:{int(video_stream.get("index"))} -r "{fps}" '
        self.video_map_cmd = _video_map_cmd

    def set_audio_map_cmd(self):
        """
        Configures the audio stream mapping command for ffmpeg based on pre-encoded audio streams.
        """
        _audio_map_cmd = ""
        audio_index = 0
        for audio_stream in self.pre_encoder.output_audio_streams:
            stream_index = int(audio_stream.get("index"))
            channels = 2
            if "channels" in audio_stream:
                channels = float(audio_stream.get("channels"))
            for opus_codec in AUDIO_OPUS_CODECS:
                if (
                        opus_codec in audio_stream.get("codec_name").lower()
                        and channels <= 2
                ):
                    acodec = OPUS_ENCODER
                    max_bitrate = 500 * 1000  # bps
                    if "bit_rate" in audio_stream:
                        abitrate = min(int(audio_stream.get("bit_rate")), max_bitrate)
                    elif "BPS-eng" in audio_stream:
                        abitrate = min(int(audio_stream.get("BPS-eng")), max_bitrate)
                    else:
                        abitrate = max_bitrate
                    _audio_map_cmd += (
                        f"-map 0:{stream_index} "
                        f"-b:a:{audio_index} {abitrate} "
                        f"-c:a:{audio_index} {acodec} "
                    )
                    self.encoded_file = self.encoded_file.with_suffix(".mkv")
                    break
            else:
                acodec = "copy"
                _audio_map_cmd += f"-map 0:{stream_index} -c:a:{audio_index} {acodec} "
            audio_index += 1
        self.audio_map_cmd = _audio_map_cmd

    def set_subtitle_map_cmd(self):
        """
        Configures the subtitle stream mapping command for ffmpeg based on pre-encoded subtitle streams.
        """
        _subtitle_map_cmd = ""
        if not self.pre_encoder.output_subtitle_streams:
            return _subtitle_map_cmd

        subtitle_index = 0
        for subtitle_stream in self.pre_encoder.output_subtitle_streams:
            scodec = "mov_text"
            stream_index = int(subtitle_stream.get("index"))
            for mkv_codec in SUBTITLE_MKV_CODECS:
                if mkv_codec in subtitle_stream.get("codec_name"):
                    scodec = "copy"
                    self.encoded_file = self.encoded_file.with_suffix(".mkv")
                    break
            _subtitle_map_cmd += (
                f"-map 0:{stream_index} -c:s:{subtitle_index} {scodec} "
            )
            subtitle_index += 1
        self.subtitle_map_cmd = _subtitle_map_cmd

    def set_encode_cmd(self):
        """
        Constructs the ffmpeg command for encoding based on current settings and options.
        """
        self.set_encoded_comment()
        self.encode_cmd = (
            f'ffmpeg -y -i "{self.original_media_file.path}" -c:v "{self.encoder}" '
            f'-crf {self.crf} {self.video_map_cmd} -metadata comment="{self.encoded_comment}" '
            f'{self.audio_map_cmd} {self.subtitle_map_cmd} "{self.encoded_file}"'
        )

    def failed_action(self, res):
        """
        Handles actions when encoding fails, including logging errors and moving files to an error directory.

        :param res: The result object from the failed ffmpeg command.
        """
        self.no_error = False
        logger.error(
            f"Encoding failed for {self.original_media_file.path}, encoder: {self.encoder} "
            f"return code: ({res.returncode}):{os.linesep}{res}"
        )
        self.error_dir = Path(BASE_ERROR_DIR) / Path(
            str(res.returncode) / self.original_media_file.relative_dir
        )
        self.error_dir.mkdir(parents=True, exist_ok=True)
        error_log = ErrorLog(self.error_dir)
        error_log.write(
            self.original_media_file.path,
            str(self.original_media_file.probe),
            res.stdout,
            res.stderr,
        )
        shutil.move(self.original_media_file.path, self.error_dir)
        self.error_output_file = self.error_dir / self.original_media_file.filename
        self.encoded_file.unlink()

    def write_success_log(self, log_date=True, update_dic: dict = None):
        """
        Logs details of the successful encoding process.

        :param log_date: Whether to include the date in the log.
        :param update_dic: Additional information to include in the log.
        """
        update_log_dict = {
            "elapsed time": {
                "total": format_timedelta(self.total_time),
                "crf checking": format_timedelta(self.pre_encoder.crf_checking_time)
                if self.pre_encoder.crf_checking_time
                else None,
                "encode": format_timedelta(
                    self.encode_time
                ),
                "target VMAF": TARGET_VMAF,
            },
            "pre encode estimated ratio": float(int(self.pre_encoder.best_ratio) / 100)
            if self.pre_encoder.best_ratio
            else None,
        }
        self.success_log_dir = self.encoded_dir
        super().write_success_log(log_date, update_log_dict)


class PhoneVideoEncoder(Encoder):
    """
    Encoder class specifically for encoding videos for iPhone.

    Inherits from Encoder and configures settings for encoding videos compatible with iPhone.
    """

    def __init__(self, media_file: MediaFile = None, args=None):
        """
        Initializes the PhoneVideoEncoder with specific paths and settings for iPhone video encoding.

        :param media_file: The media file to be encoded.
        :param args: Additional arguments for encoding.
        """
        super().__init__(media_file, args=args)
        self.encoded_dir = os.path.abspath(OUTPUT_DIR_IPHONE)
        self.encoder = VIDEO_CODEC_IPHONE_XR
        self.cmd_options = IPHONE_XR_OPTIONS
        self.success_log_dir = os.getcwd()

    def write_success_log(self, log_date=False, update_dic: dict = None):
        """
        Writes a success log for iPhone video encoding.

        :param log_date: Flag to include the date in the log.
        :param update_dic: Additional information to include in the log.
        """
        super().write_success_log(log_date, update_dic)

    def post_actions(self):
        """
        Performs post-encoding actions specific to iPhone videos, such as logging success.
        """
        logger.success(
            f"{os.path.relpath(self.original_media_file.path)} ({format_timedelta(self.encode_time)})"
        )

    def set_encoded_comment(self):
        """
        Sets the metadata comment for the iPhone encoded video.
        """
        comment_dic = {
            "comment": VIDEO_COMMENT_ENCODED,
            "source file": self.original_media_file.filename,
            "source file size": formatted_size(self.original_media_file.size),
        }
        self.encoded_comment = yaml.dump(
            comment_dic,
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=99999,
        )

    def encode(self):
        """
        Starts the encoding process for iPhone videos. Sets the appropriate encoding command and handles errors.
        """
        self.set_encoded_comment()
        self.encoded_file = self.original_media_file.path.with_suffix(".mp4")
        os.makedirs(self.encoded_dir, exist_ok=True)
        self.encode_cmd = (
            f'ffmpeg -y -i "{self.original_media_file.path.as_posix()}" '
            f"{self.cmd_options}"
            f"-vcodec {VIDEO_CODEC_IPHONE_XR} -acodec {AUDIO_CODEC_IPHONE_XR} "
            f"-b:v {MANUAL_VIDEO_BIT_RATE_IPHONE_XR} -b:a {MANUAL_AUDIO_BIT_RATE_IPHONE_XR} "
            f'-metadata comment="{self.encoded_comment}" '
            f'"{self.encoded_file}"'
        )

        show_cmd = __debug__

        cmd_path = self.encoded_dir / Path(COMMAND_TEXT)
        res = run_cmd(
            self.encode_cmd,
            show_cmd=show_cmd,
            cmd_path=cmd_path,
        )
        if res.returncode == 0:
            self.no_error = True
            if self.keep_mtime:
                os.utime(
                    self.encoded_file,
                    (
                        datetime.now().timestamp(),
                        os.path.getmtime(self.original_media_file.path),
                    ),
                )
            self.encoded_size = os.path.getsize(self.encoded_file)
        else:
            self.failed_action(res)


class AudioEncoder(Encoder):
    """
    Encoder class specifically for encoding audio files.

    Inherits from Encoder and provides functionality for encoding audio using different codecs.
    """

    def __init__(
            self,
            media_file: MediaFile,
            target_bit_rate: int = TARGET_BIT_RATE_IPHONE_XR,
            args=None,
    ):
        """
        Initializes the AudioEncoder with specific paths and settings for audio encoding.

        :param media_file: The media file to be encoded.
        :param target_bit_rate: Target bit rate for the audio encoding.
        :param args: Additional arguments for encoding.
        """
        super().__init__(media_file=media_file, args=args)
        self.encoder = DEFAULT_AUDIO_ENCODER
        self.target_bit_rate = target_bit_rate
        self.encoded_dir = AUDIO_ENCODED_ROOT_DIR / self.original_media_file.path.parent.relative_to(Path.cwd())
        self.encoded_file = (
                self.encoded_dir
                / self.original_media_file.path.with_suffix(self._get_file_extension()).name
        )

        self.encoded_raw_dir = AUDIO_ENCODED_RAW_DIR / self.original_media_file.path.parent.relative_to(Path.cwd())
        self.encoded_root_dir = VIDEO_OUT_DIR_ROOT.absolute()
        self.error_dir = BASE_ERROR_DIR
        self.success_log_dir = self.encoded_dir

    def _get_file_extension(self) -> str:
        """
        Determines the file extension based on the encoder being used.

        :return: File extension for the encoded audio file.
        """
        if self.encoder == "libopus":
            return ".opus"
        elif self.encoder == "libmp3lame":
            return ".mp3"
        else:
            logger.error("Unknown encoder detected!")
            return "unknown"

    def set_encoded_comment(self):
        """
        Sets the metadata comment for the encoded audio file.

        The comment includes details about the encoding process and source file.
        """
        comment_dic = {
            "comment": AUDIO_COMMENT_ENCODED,
            "source file": self.original_media_file.filename,
            "source file size": formatted_size(self.original_media_file.size),
        }
        self.encoded_comment = yaml.dump(
            comment_dic,
            default_flow_style=True,
            sort_keys=False,
            allow_unicode=True,
            width=99999,
        ).strip()

    def encode(self):
        """
        Starts the encoding process for the audio file.

        Sets the encoding command and executes it. If encoding is successful, updates the file's metadata
        and handles errors if the encoding fails.
        """
        self.set_encoded_comment()
        self.encode_cmd = (
            f'ffmpeg -y -i "{self.original_media_file.path}" '
            f"-acodec {self.encoder} "
            f"-b:a {self.target_bit_rate} "
            f'-metadata comment="{self.encoded_comment}" '
            f'"{self.encoded_file}"'
        )
        show_cmd = __debug__
        cmd_path = self.encoded_dir / COMMAND_TEXT
        res = run_cmd(
            self.encode_cmd,
            show_cmd=show_cmd,
            cmd_path=cmd_path,
        )
        if res.returncode == 0:
            self.no_error = True
            if self.keep_mtime:
                os.utime(
                    self.encoded_file,
                    (
                        datetime.now().timestamp(),
                        os.path.getmtime(self.original_media_file.path),
                    ),
                )
            self.encoded_size = os.path.getsize(self.encoded_file)
        else:
            self.failed_action(res)

    def failed_action(self, res):
        """
        Handles actions if the encoding fails, including logging errors and moving files to an error directory.

        :param res: The result object from the failed ffmpeg command.
        """
        self.no_error = False
        logger.error(
            f"Encoding failed for audio file. {self.original_media_file.path}, "
            f"return code: ({res.returncode}):{os.linesep}{res}"
        )
        # Ensure all path components are strings
        error_dir = (
                BASE_ERROR_DIR / str(res.returncode) / self.original_media_file.relative_dir
        )
        error_dir.mkdir(parents=True, exist_ok=True)
        error_log = ErrorLog(error_dir)
        error_log.write(
            self.original_media_file.path,
            str(self.original_media_file.probe),
            res.stdout,
            res.stderr,
        )
        # Move original file to the error directory
        shutil.move(str(self.original_media_file.path), error_dir)
        self.error_output_file = os.path.join(
            error_dir, self.original_media_file.filename
        )
        os.remove(self.encoded_file)
