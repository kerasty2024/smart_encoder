import hashlib
import re
import shutil
from pathlib import Path
from pprint import pformat

import ffmpeg
from loguru import logger

# Assuming exceptions.py is in the same package (src.domain)
# 修正点: MediaFileException をインポートに追加
from .exceptions import NoDurationFoundException, MediaFileException

# Assuming common.py is in src.config
from ..config.common import LOAD_FAILED_LOG, LOAD_FAILED_DIR


def parse_duration(duration_str: str) -> float:  # Added type hint for duration_str
    """
    Parses a duration string into total seconds.

    This function is designed to handle two common duration formats provided by ffprobe:
    1. A simple string representing a floating-point number of seconds (e.g., "3600.5").
    2. A timecode string in the format 'HH:MM:SS.sss' (e.g., "01:00:00.500").
       Hours and minutes are optional in the timecode format.

    Args:
        duration_str: The string containing the duration to parse.

    Returns:
        The total duration in seconds as a float. Returns 0.0 if parsing fails.
    """
    try:
        return float(duration_str)
    except ValueError:
        # Regex for HH:MM:SS.sss, H:MM:SS.sss, MM:SS.sss, M:SS.sss
        pattern = r"(?:(\d{1,2}):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)"
        match = re.fullmatch(
            pattern, duration_str
        )  # Use fullmatch for stricter parsing
        if match:
            hours_str, minutes_str, seconds_str = match.groups()
            hours = int(hours_str) if hours_str else 0
            minutes = (
                int(minutes_str) if minutes_str else 0
            )  # Should always exist if pattern matches
            seconds = float(seconds_str)
            return float(hours * 3600 + minutes * 60 + seconds)
        logger.warning(f"Could not parse duration string: {duration_str}")
    return 0.0


class MediaFile:
    """
    Represents a single media file and provides a clean interface to its metadata.

    This class is a core component of the domain layer. When instantiated with a
    file path, it automatically uses `ffprobe` (via the ffmpeg-python library)
    to analyze the file. It then populates its attributes with essential
    information like duration, streams (video, audio, subtitle), codecs,
    bitrate, and file hashes.

    The primary purpose of this class is to provide a standardized, easy-to-access
    representation of a media file's properties, abstracting away the complexities
    of running `ffprobe` and parsing its JSON output.

    If the file cannot be probed or is found to be invalid (e.g., has no duration),
    the constructor will raise an exception and attempt to move the problematic
    file to a designated error directory to prevent it from being processed again.

    Attributes:
        path (Path): The absolute path to the media file.
        filename (str): The name of the file, including its extension.
        stem (str): The 'stem' of the filename, correctly handling suffixes like '.!qB'.
        relative_dir (Path): The file's directory relative to the current working directory.
        size (int): The size of the file in bytes.
        probe (dict | None): The raw `ffprobe` output as a nested dictionary. None if probing failed.
        duration (float): The duration of the media in seconds.
        comment (str): The comment metadata tag from the file, if present.
        video_stream_count (int): The number of video streams found.
        video_streams (list): A list of dictionaries, each representing a video stream.
        audio_streams (list): A list of dictionaries, each representing an audio stream.
        subtitle_streams (list): A list of dictionaries, each representing a subtitle stream.
        vcodec (str): The codec name of the first video stream (e.g., 'h264', 'hevc'), lowercased.
        vbitrate (int): The bitrate of the first video stream in bits per second.
        md5 (str): The MD5 hash of the file, used for quick identification.
        sha256 (str): The SHA256 hash of the file, for more robust integrity checking.
        load_failed_dir (Path): The directory where this file will be moved if it fails to load.
    """

    def __init__(self, path: Path):
        """
        Initializes the MediaFile object by probing the file at the given path.

        This constructor performs several critical initializations:
        1. Resolves the file path to an absolute path for consistency.
        2. Gathers basic file system info like size and name.
        3. Calls `set_probe()` to run `ffprobe` and populates the `probe` attribute.
        4. If probing is successful, it calls other `set_*` methods to parse the
           probe data and populate attributes like duration, streams, codecs, etc.
        5. Calculates file hashes for integrity checks and identification.

        If any critical step fails (like probing or finding a duration), this
        constructor will call `handle_load_failure()` to move the file to an
        error directory and then re-raise an appropriate exception to halt
        further processing of this invalid file.

        Args:
            path: The `pathlib.Path` object pointing to the media file.

        Raises:
            FileNotFoundError: If the file does not exist at the given path.
            NoDurationFoundException: If the media file's duration cannot be determined,
                                      which is a critical failure.
            MediaFileException: For other ffmpeg-related errors or unexpected issues
                                during initialization.
        """
        if not path.exists():
            # This helps catch issues earlier if a file path is incorrect.
            logger.error(
                f"MediaFile initialization error: File does not exist at {path}"
            )
            raise FileNotFoundError(f"Media file not found: {path}")

        self.path: Path = path.resolve()  # Always work with absolute paths internally
        self.filename: str = self.path.name
        self.stem: str = self._get_clean_stem()
        # Ensure CWD is what's expected or pass a base_path argument
        try:
            self.relative_dir: Path = self.path.parent.relative_to(Path.cwd())
        except ValueError:
            # If path is not relative to CWD, store parent or full path segment
            logger.warning(
                f"File {self.path} is not relative to CWD {Path.cwd()}. Storing full parent dir name."
            )
            self.relative_dir = self.path.parent.name  # Or handle as appropriate

        self.size: int = self.path.stat().st_size
        self.probe: dict | None = None  # Use dict | None for Python 3.10+
        self.duration: float = 0  # in seconds
        self.comment: str = ""
        self.video_stream_count: int = 0
        self.video_streams: list = []
        self.audio_streams: list = []
        self.subtitle_streams: list = []
        self.vcodec: str = ""
        self.vbitrate: int = 0  # bits per second
        self.md5: str = ""
        self.sha256: str = ""

        # errors
        # LOAD_FAILED_DIR is already an absolute path from config.common
        self.load_failed_dir: Path = LOAD_FAILED_DIR / self.relative_dir

        try:
            self.set_probe()  # This can raise ffmpeg.Error
            if self.probe:  # Only proceed if probe was successful
                self.set_duration()  # This can raise NoDurationFoundException
                self.set_comment()
                self.set_video_stream_count()
                self.set_vcodec()
                self.set_vbitrate()
                self.set_streams()
                self.set_hashes()  # Hashes can be set even if some metadata is missing
            else:
                # If probe is None after set_probe (due to handled error path),
                # it means the file was moved. We should not proceed.
                # The original code would continue and likely fail.
                # A custom exception could be raised here to signal MediaFile creation failure.
                logger.error(
                    f"Probe failed for {self.path}, MediaFile object may be incomplete."
                )
                # Depending on strictness, could raise an exception here.
                # For now, let it be, as set_probe handles moving the file.

        except NoDurationFoundException as e:
            logger.error(f"Critical error for {self.path}: {e}")
            # Re-raise or handle. Original code in start_encode_video_file moves the file.
            # If MediaFile constructor fails, this should be clear to the caller.
            self.handle_load_failure(reason="NoDurationFound")  # Ensure file is moved
            raise  # Re-raise the exception to be caught by the caller

        except (
            ffmpeg.Error
        ) as e:  # Catch ffmpeg.Error from set_probe if it wasn't handled internally
            logger.error(
                f"ffmpeg.Error during MediaFile init for {self.path}: {e.stderr}"
            )
            self.handle_load_failure(reason="ffmpegProbeError")
            # Raise a more specific custom exception if needed
            raise MediaFileException(
                f"Failed to probe media file {self.path}: {e.stderr}"
            ) from e

        except Exception as e:  # Catch-all for other unexpected errors during init
            logger.error(
                f"Unexpected error initializing MediaFile for {self.path}: {e}"
            )
            self.handle_load_failure(reason="UnexpectedInitError")
            raise MediaFileException(f"Unexpected error for {self.path}: {e}") from e

    def _get_clean_stem(self) -> str:
        """
        Gets the 'stem' of the filename, correctly handling multi-part extensions
        like '.mkv.!qB'.
        """
        name = self.filename
        # First, remove known temporary suffixes like .!qB
        if name.lower().endswith(".!qb"):
            name = name[:-4]

        # Now, use pathlib's stem on the potentially cleaned name
        return Path(name).stem

    def set_hashes(self):
        """
        Calculates and sets the MD5 and SHA256 hashes of the file.

        This method reads the file in binary mode to compute the hashes. It is
        called during initialization. If the file has been moved or deleted
        before this method is called, it will log a warning and do nothing.
        """
        if not self.path.exists():
            logger.warning(
                f"Cannot set hashes for {self.filename}, file no longer at {self.path}"
            )
            return
        try:
            with self.path.open(mode="rb") as f:
                self.md5 = hashlib.file_digest(f, "md5").hexdigest()
            with self.path.open(mode="rb") as f:  # Re-open or f.seek(0)
                self.sha256 = hashlib.file_digest(f, "sha256").hexdigest()
        except Exception as e:
            logger.error(f"Error calculating hashes for {self.path}: {e}")
            self.md5 = "error"
            self.sha256 = "error"

    def set_probe(self):
        """
        Probes the media file using `ffmpeg.probe` to extract all metadata.

        The result, a large nested dictionary, is stored in `self.probe`.
        If probing fails with an `ffmpeg.Error`, this method sets `self.probe`
        to None and re-raises the exception to be handled by the constructor,
        which will then trigger `handle_load_failure`.
        """
        if (
            not self.path.exists()
        ):  # Check if file exists before probing (might have been moved)
            logger.warning(
                f"Probe skipped for {self.filename}, file no longer at {self.path}"
            )
            self.probe = None
            return
        try:
            self.probe = ffmpeg.probe(str(self.path))
            logger.debug(f"Probe data for {self.filename}:\n{pformat(self.probe)}")
        except ffmpeg.Error as e:
            logger.error(f"ffmpeg.probe failed for {self.path}: {e.stderr}")
            # self.handle_load_failure should be called by the constructor's error handling
            # or, if set_probe is called standalone, it should handle it.
            # For constructor flow, we'll let the constructor's except block call handle_load_failure.
            self.probe = None  # Signal that probe failed
            raise  # Re-raise to be caught by constructor's ffmpeg.Error handler

    def handle_load_failure(self, reason: str = "UnknownProbeFailure"):
        """
        Handles cases where probing or initializing the media file fails.

        This crucial error-handling function moves the problematic file from its
        original location to a designated error directory (`load_failed`).
        This prevents the application from repeatedly trying to process a file
        that is corrupted, unreadable, or otherwise invalid. It also logs the
        original and new paths to a text file for later review.

        Args:
            reason: A short string explaining why the failure occurred.
        """
        if not self.path.exists():
            logger.warning(
                f"Handle_load_failure called for {self.filename}, but file no longer at {self.path}. It might have been moved already."
            )
            return

        self.load_failed_dir.mkdir(parents=True, exist_ok=True)
        try:
            # Ensure unique path in error_dir
            unique_error_path = (
                self.path.parent / self.load_failed_dir / self.path.name
            )  # Target directory for unique name check

            target_path_in_error_dir = self.load_failed_dir / self.filename
            counter = 0
            base_name = self.path.stem
            suffix = self.path.suffix

            while target_path_in_error_dir.exists():
                counter += 1
                target_path_in_error_dir = (
                    self.load_failed_dir / f"{base_name}_{counter}{suffix}"
                )

            shutil.move(str(self.path), str(target_path_in_error_dir))
            logger.info(
                f"Moved corrupted/unreadable file {self.filename} to {target_path_in_error_dir} due to: {reason}"
            )

            # Log the original path and new path
            # LOAD_FAILED_LOG is absolute from config.common
            LOAD_FAILED_LOG.parent.mkdir(
                parents=True, exist_ok=True
            )  # Ensure log directory exists
            with LOAD_FAILED_LOG.open("a", encoding="utf-8") as log_file:
                log_file.write(
                    f"Original: {self.path}, Moved to: {target_path_in_error_dir}, Reason: {reason}\n"
                )

            # self.path = target_path_in_error_dir # Update path if MediaFile object is to remain valid,
            # but typically this object should be discarded.
            # For now, self.path remains the original, now non-existent path.
        except OSError as e:
            logger.error(
                f"OSError moving failed file {self.filename} to {self.load_failed_dir}: {e}"
            )
        except Exception as e:
            logger.error(
                f"Unexpected error in handle_load_failure for {self.filename}: {e}"
            )

    def get_unique_path(self, directory: Path) -> Path:
        """
        Generates a unique file path in a target directory.

        If a file with the same name already exists in the `directory`, this
        method appends a numeric suffix (e.g., `_1`, `_2`) to the filename
        until a unique path is found.

        Args:
            directory: The target directory where the file should be placed.

        Returns:
            A `pathlib.Path` object representing a unique file path.

        Raises:
            RuntimeError: If a unique path cannot be found after many attempts.
        """
        target_path = directory / self.path.name
        if not target_path.exists():
            return target_path

        base, ext = self.path.stem, self.path.suffix
        i = 0
        while True:
            i += 1
            new_name = f"{base}_{i}{ext}"
            new_path = directory / new_name
            if not new_path.exists():
                return new_path
            if i > 10000:  # Safety break
                logger.error(
                    f"Could not find unique path for {self.path.name} in {directory} after {i} tries."
                )
                # Fallback or raise error
                raise RuntimeError(
                    f"Cannot generate a unique file name in {directory} for {self.path.name}"
                )

    def set_duration(self):
        """
        Sets the duration of the media file from the probe data.

        It first looks for the duration in the 'format' section of the probe
        data, which is the most reliable source. If not found, it checks
        individual streams. As a last resort, it calls a helper method to
        calculate duration from frame count and frame rate.

        Raises:
            NoDurationFoundException: If a valid, positive duration cannot be determined.
        """
        if not self.probe:  # Should have been checked by constructor
            logger.error(
                f"Cannot set duration for {self.filename}: probe data is missing."
            )
            raise NoDurationFoundException(
                f"No probe data to find duration for {self.path}"
            )

        duration_val = None
        # Check format duration first
        if "format" in self.probe and "duration" in self.probe["format"]:
            duration_val = self.probe["format"]["duration"]

        # If not in format, check streams
        if not duration_val and "streams" in self.probe:
            for stream in self.probe["streams"]:
                if "duration" in stream:
                    duration_val = stream["duration"]
                    break  # Take the first duration found in streams

        if duration_val is not None:
            self.duration = parse_duration(str(duration_val))
        else:  # Fallback if 'duration' key is nowhere
            logger.warning(
                f"Primary 'duration' key not found for {self.filename}. Trying to calculate from video stream."
            )
            self.duration = self._calculate_duration_from_video_stream_data()

        if self.duration <= 0:
            logger.error(
                f"Failed to get a valid positive duration for {self.path}. Calculated: {self.duration}"
            )
            raise NoDurationFoundException(
                f"No valid (positive) duration found for {self.path}"
            )
        logger.debug(f"Duration for {self.filename}: {self.duration}s")

    def _calculate_duration_from_video_stream_data(self) -> float:
        """
        Calculates duration as a fallback using video frame count and frame rate.

        This method is less reliable than reading the duration tag directly but
        can provide a reasonable estimate if the primary 'duration' field is

        missing from the ffprobe output.

        Returns:
            The calculated duration in seconds, or 0.0 if calculation is not possible.
        """
        if not self.probe or "streams" not in self.probe:
            return 0.0

        video_stream = next(
            (s for s in self.probe["streams"] if s.get("codec_type") == "video"), None
        )

        if not video_stream:
            logger.debug(f"No video stream to calculate duration for {self.filename}")
            return 0.0

        nb_frames_str = video_stream.get("nb_frames")
        avg_frame_rate_str = video_stream.get("avg_frame_rate")

        if nb_frames_str and avg_frame_rate_str and avg_frame_rate_str != "0/0":
            try:
                nb_frames = int(nb_frames_str)
                num, den = map(int, avg_frame_rate_str.split("/"))
                if den == 0:  # Avoid division by zero
                    logger.warning(
                        f"Invalid frame rate denominator (0) for {self.filename}"
                    )
                    return 0.0
                avg_frame_rate = num / den
                if nb_frames > 0 and avg_frame_rate > 0:
                    calculated_duration = nb_frames / avg_frame_rate
                    logger.debug(
                        f"Calculated duration from frames/fps for {self.filename}: {calculated_duration}s"
                    )
                    return calculated_duration
            except ValueError as e:
                logger.warning(
                    f"Could not parse nb_frames/avg_frame_rate for {self.filename}: {e}"
                )
            except ZeroDivisionError:
                logger.warning(
                    f"Zero division error calculating duration from fps for {self.filename}"
                )

        logger.debug(
            f"Could not calculate duration from video stream data for {self.filename}"
        )
        return 0.0

    def set_comment(self):
        """
        Extracts and sets the 'comment' metadata tag from the probe data.

        This is used to check if a file has already been processed by this application.
        """
        if not self.probe:
            return
        format_info = self.probe.get("format", {})
        self.comment = format_info.get("tags", {}).get("comment", "")
        logger.debug(f"Comment for {self.filename}: '{self.comment}'")

    def set_video_stream_count(self):
        """
        Counts and sets the number of video streams found in the media file.
        """
        if not self.probe:
            return
        self.video_stream_count = sum(
            1
            for stream in self.probe.get("streams", [])
            if stream.get("codec_type") == "video"
        )
        logger.debug(
            f"Video stream count for {self.filename}: {self.video_stream_count}"
        )

    def set_vcodec(self):
        """
        Finds the first video stream and sets its codec name, lowercased.
        """
        if not self.probe:
            return
        for stream in self.probe.get("streams", []):
            if stream.get("codec_type") == "video":
                self.vcodec = stream.get("codec_name", "").lower()
                logger.debug(f"Video codec for {self.filename}: {self.vcodec}")
                return  # Found first video stream
        self.vcodec = ""  # No video stream found

    def set_vbitrate(self):
        """

        Sets the video bitrate (in bps) from the first video stream's probe data.

        If the 'bit_rate' field is not available directly in the stream data,
        it calculates an approximate overall bitrate for the file by dividing
        the total file size by its duration.
        """
        if not self.probe:
            return

        video_stream_found = False
        for stream in self.probe.get("streams", []):
            if stream.get("codec_type") == "video":
                bit_rate_str = stream.get("bit_rate")
                if bit_rate_str:
                    try:
                        self.vbitrate = int(bit_rate_str)
                        video_stream_found = True
                        break
                    except ValueError:
                        logger.warning(
                            f"Could not parse video bit_rate '{bit_rate_str}' for {self.filename}"
                        )
                # If 'bit_rate' is not present or invalid, it will fall through to calculation
                video_stream_found = True  # Mark that a video stream was at least found
                break  # Process first video stream

        if (
            not self.vbitrate and video_stream_found and self.duration > 0
        ):  # If bitrate couldn't be read but video exists
            calculated_bitrate = int(8 * self.size / self.duration)  # bps
            logger.debug(
                f"Video bitrate for {self.filename} not found in stream, calculated: {calculated_bitrate} bps from total size/duration."
            )
            self.vbitrate = calculated_bitrate
        elif not video_stream_found:
            self.vbitrate = 0  # No video stream
            logger.debug(
                f"No video stream found for {self.filename}, video bitrate set to 0."
            )

        logger.debug(f"Final video bitrate for {self.filename}: {self.vbitrate} bps")

    def set_streams(self):
        """
        Iterates through all streams in the probe data and categorizes them.

        Populates the `self.video_streams`, `self.audio_streams`, and
        `self.subtitle_streams` lists with the corresponding stream dictionaries.
        """
        if not self.probe or "streams" not in self.probe:
            return

        self.video_streams = []
        self.audio_streams = []
        self.subtitle_streams = []

        for stream in self.probe.get("streams", []):
            codec_type = stream.get("codec_type")
            if codec_type == "video":
                self.video_streams.append(stream)
            elif codec_type == "audio":
                self.audio_streams.append(stream)
            elif codec_type == "subtitle":
                self.subtitle_streams.append(stream)
            elif codec_type not in {
                "data",
                "attachment",
            }:  # Common non-A/V stream types to ignore silently
                logger.warning(
                    f"Other type of stream found in {self.filename} (type: {codec_type}):\n{pformat(stream)}"
                )
        logger.debug(f"For {self.filename}: {len(self.video_streams)} video, {len(self.audio_streams)} audio, {len(self.subtitle_streams)} subtitle streams.")