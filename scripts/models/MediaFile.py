import hashlib
import re
import shutil
from pathlib import Path
from pprint import pformat

import ffmpeg
from loguru import logger

from scripts.models.PreVideoEncodeExceptions import NoDurationFoundException
from scripts.settings.common import LOAD_FAILED_LOG, LOAD_FAILED_DIR


def parse_duration(duration):
    """Parse a duration string formatted as 'HH:MM:SS.sss' into seconds."""
    try:
        return float(duration)
    except ValueError:
        pattern = r"(?:(\d+):)?(\d+):(\d+.\d+)"
        match = re.match(pattern, duration)
        if match:
            hours = int(match.group(1)) if match.group(1) else 0
            minutes = int(match.group(2))
            seconds = float(match.group(3))
            return hours * 3600 + minutes * 60 + seconds
    return 0.0


class MediaFile:
    """
    A class to represent a media file and extract its metadata.

    Attributes:
        path (Path): The file path of the media file.
        filename (str): The name of the file.
        relative_dir (str): The relative directory of the file from the current working directory.
        size (int): The size of the file in bytes.
        probe (dict): Metadata probe data from ffmpeg.
        duration (float): Duration of the media file in seconds.
        comment (str): Comment extracted from the file's metadata.
        video_stream_count (int): Number of video streams in the file.
        video_streams (list): List of video stream dictionaries.
        audio_streams (list): List of audio stream dictionaries.
        subtitle_streams (list): List of subtitle stream dictionaries.
        vcodec (str): Video codec used in the file.
        vbitrate (int): Video bitrate of the file.
        md5 (str): MD5 hash of the file.
        sha256 (str): SHA256 hash of the file.
        load_failed_dir (Path): Directory where files that fail to load are moved.

    Methods:
        __init__(path: Path): Initializes the MediaFile object.
        set_hashes(): Calculates and sets the MD5 and SHA256 hashes of the file.
        set_probe(): Probes the media file to extract metadata; moves file to error directory if probing fails.
        handle_load_failure(): Handles file move to the error directory and logs the failure.
        get_unique_path(directory: Path) -> Path: Generates a unique file path in the specified directory.
        set_duration(): Sets the duration of the media file from the probe data.
        set_comment(): Sets the comment from the probe data.
        set_video_stream_count(): Counts the number of video streams in the file.
        set_vcodec(): Sets the video codec from the probe data.
        set_vbitrate(): Sets the video bitrate from the probe data or calculates it.
        set_streams(): Categorizes the streams into video, audio, and subtitle streams.
    """

    def __init__(self, path: Path):
        """
        Initializes the MediaFile object.

        :param path: The file path of the media file as a Path object.
        """
        self.path: Path = path
        self.filename: str = self.path.name
        self.relative_dir: Path = self.path.relative_to(Path.cwd()).parent
        self.size: int = self.path.stat().st_size
        self.probe = None
        self.duration: float = 0  # in seconds
        self.comment = ""
        self.video_stream_count = 0
        self.video_streams = []
        self.audio_streams = []
        self.subtitle_streams = []
        self.vcodec = ""
        self.vbitrate = 0
        self.md5 = ""
        self.sha256 = ""

        # errors
        self.load_failed_dir: Path = Path(LOAD_FAILED_DIR) / self.relative_dir

        self.set_probe()
        self.set_duration()
        self.set_video_stream_count()
        self.set_vcodec()
        self.set_vbitrate()
        self.set_streams()
        self.set_hashes()

    def set_hashes(self):
        """
        Calculates and sets the MD5 and SHA256 hashes of the file.
        """
        with self.path.open(mode="rb") as f:
            self.md5 = hashlib.file_digest(f, "md5").hexdigest()
            self.sha256 = hashlib.file_digest(f, "sha256").hexdigest()

    def set_probe(self):
        """
        Probes the media file using ffmpeg to extract metadata. If probing fails, moves the file to the error directory
        and logs the failure.

        :return: None
        """
        try:
            self.probe = ffmpeg.probe(
                str(self.path)
            )  # ffmpeg may not accept Path objects directly
            logger.debug(pformat(self.probe))
        except ffmpeg.Error:
            logger.error(f"File cannot be read: {self.path}")
            self.load_failed_dir.mkdir(parents=True, exist_ok=True)
            self.handle_load_failure()

    def handle_load_failure(self):
        """
        Handles the case where probing the media file fails. Moves the file to the error directory and logs the failure.
        """
        try:
            new_path = self.get_unique_path(self.load_failed_dir)
            shutil.move(str(self.path), str(new_path))
            with Path(LOAD_FAILED_LOG).open("a", encoding="utf-8") as log_file:
                log_file.write(f"{self.path} (renamed to: {new_path.name})\n")
        except OSError as e:
            logger.error(f"Error handling load failure: {e}")

    def get_unique_path(self, directory: Path) -> Path:
        """
        Generates a unique file path in the specified directory by appending a numeric suffix if needed.

        :param directory: The directory where the unique path should be created.
        :return: A unique Path object.
        """
        if not (directory / self.path.name).exists():
            return directory / self.path.name
        base, ext = self.path.stem, self.path.suffix
        for i in range(10000):
            new_name = f"{base}_{i}{ext}"
            new_path = directory / new_name
            if not new_path.exists():
                return new_path
        raise RuntimeError("Cannot generate a unique file name")

    def set_duration(self):
        """
        Sets the duration of the media file from the probe data.
        Raises an error if duration cannot be determined.
        """
        if not self.probe:
            raise NoDurationFoundException(f"No probe data found for file {self.path}")

        # Attempt to find duration in format or streams
        duration_sources = [
            self.probe.get("format", {}),
            *self.probe.get("streams", []),
        ]

        for source in duration_sources:
            for key in ["duration", "DURATION"]:
                if key in source:
                    self.duration = parse_duration(source[key])
                    if self.duration > 0:
                        return

        # Fallback: Calculate duration by decoding frames
        self.duration = self.calculate_duration_by_decoding()

        if self.duration <= 0:
            raise NoDurationFoundException(f"Failed to get duration! {self.path}")

    def calculate_duration_by_decoding(self):
        """Calculate video duration by decoding frames using ffmpeg."""
        try:
            video_stream = next(
                (
                    stream
                    for stream in self.probe["streams"]
                    if stream["codec_type"] == "video"
                ),
                None,
            )

            if not video_stream:
                return 0.0

            # Get the average frame rate
            avg_frame_rate = video_stream.get("avg_frame_rate")
            if not avg_frame_rate:
                return 0.0
            num, denom = map(int, avg_frame_rate.split("/"))
            frame_rate = num / denom

            # Get the number of frames
            nb_frames = int(video_stream.get("nb_frames", 0))
            if nb_frames > 0:
                return nb_frames / frame_rate

        except ffmpeg.Error as e:
            print(f"An error occurred while decoding frames: {e}")
            return 0.0

        return 0.0

    def set_comment(self):
        """
        Sets the comment from the probe data.

        :return: None
        """
        format_info = self.probe.get("format", {})
        self.comment = format_info.get("tags", {}).get("comment", "")

    def set_video_stream_count(self):
        """
        Counts the number of video streams in the media file.

        :return: None
        """
        self.video_stream_count = sum(
            1
            for stream in self.probe.get("streams", [])
            if stream.get("codec_type") == "video"
        )

    def set_vcodec(self):
        """
        Sets the video codec from the probe data.

        :return: None
        """
        for stream in self.probe.get("streams", []):
            if stream.get("codec_type") == "video":
                self.vcodec = stream.get("codec_name", "").lower()
                break

    def set_vbitrate(self):
        """
        Sets the video bitrate from the probe data or calculates it based on file size and duration.

        :return: None
        """
        for stream in self.probe.get("streams", []):
            if stream.get("codec_type") == "video":
                self.vbitrate = int(
                    stream.get("bit_rate", 8 * self.size / self.duration)
                )
                break

    def set_streams(self):
        """
        Categorizes the streams into video, audio, and subtitle streams based on their codec type.

        :return: None
        """
        for stream in self.probe.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                self.video_streams.append(stream)
            elif codec_type == "audio":
                self.audio_streams.append(stream)
            elif codec_type == "subtitle":
                self.subtitle_streams.append(stream)
            elif codec_type not in {"data", "attachment"}:
                logger.warning(f"Other type of stream found ({codec_type}):\n{stream}")
