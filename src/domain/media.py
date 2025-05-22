import hashlib
import re
import shutil
from pathlib import Path
from pprint import pformat

import ffmpeg
from loguru import logger

# Assuming exceptions.py is in the same package (src.domain)
from .exceptions import NoDurationFoundException

# Assuming common.py is in src.config
from ..config.common import LOAD_FAILED_LOG, LOAD_FAILED_DIR


def parse_duration(duration_str: str) -> float:  # Added type hint for duration_str
    """Parse a duration string formatted as 'HH:MM:SS.sss' or float string into seconds."""
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
    A class to represent a media file and extract its metadata.
    """

    def __init__(self, path: Path):
        """
        Initializes the MediaFile object.

        :param path: The file path of the media file as a Path object.
        """
        if not path.exists():
            # This helps catch issues earlier if a file path is incorrect.
            logger.error(
                f"MediaFile initialization error: File does not exist at {path}"
            )
            raise FileNotFoundError(f"Media file not found: {path}")

        self.path: Path = path.resolve()  # Always work with absolute paths internally
        self.filename: str = self.path.name
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

    def set_hashes(self):
        """
        Calculates and sets the MD5 and SHA256 hashes of the file.
        Only attempts if the file still exists at self.path.
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
        Probes the media file using ffmpeg to extract metadata.
        If probing fails, calls handle_load_failure which moves the file.
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
        Handles the case where probing or initializing the media file fails.
        Moves the file to an error directory and logs the failure.
        The file at self.path is moved, so self.path becomes invalid afterwards.
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
        Generates a unique file path in the specified directory by appending a numeric suffix if needed.
        (This seems to be a general utility, perhaps could be in a utils module if used elsewhere)
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
        Raises NoDurationFoundException if duration cannot be determined.
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
        Tries to calculate duration from video stream's nb_frames and avg_frame_rate.
        This is a fallback if the 'duration' field is not directly available.
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
        Sets the comment from the probe data.
        """
        if not self.probe:
            return
        format_info = self.probe.get("format", {})
        self.comment = format_info.get("tags", {}).get("comment", "")
        logger.debug(f"Comment for {self.filename}: '{self.comment}'")

    def set_video_stream_count(self):
        """
        Counts the number of video streams in the media file.
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
        Sets the video codec from the probe data (first video stream).
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
        Sets the video bitrate from the probe data (first video stream) or calculates it.
        Bitrate in bps.
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
        Categorizes the streams into video, audio, and subtitle streams.
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
            }: # Common non-A/V stream types to ignore silently
                logger.warning(
                    f"Other type of stream found in {self.filename} (type: {codec_type}):\n{pformat(stream)}"
                )
        logger.debug(f"For {self.filename}: {len(self.video_streams)} video, {len(self.audio_streams)} audio, {len(self.subtitle_streams)} subtitle streams.")