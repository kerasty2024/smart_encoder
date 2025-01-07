import argparse
import collections
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from faster_whisper import WhisperModel
from loguru import logger

if "encode" in __file__:
    from scripts.models.Log import ErrorLog
    from scripts.settings.common import LANGUAGE_WORDS


def run_cmd(
    cmd: str,
    src: Path = Path(),
    dst: Path = Path(),
    show_cmd: bool = False,
    cmd_path: Optional[Path] = None,
) -> Optional[subprocess.CompletedProcess]:
    """
    Executes a shell command and logs the output.

    :param cmd: The command to execute.
    :param src: Path to the source file for error logging.
    :param dst: Directory path for error logging.
    :param show_cmd: If True, logs the command before execution.
    :param cmd_path: If provided, appends the command to this file.
    :return: Result of the subprocess run, or None if an exception occurs.
    """
    if show_cmd:
        logger.debug(f"Executing command: {cmd}")

    if cmd_path:
        with cmd_path.open("a", encoding="utf-8") as cmd_file:
            print(cmd, file=cmd_file)

    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    except Exception as e:
        logger.error(f"Error executing command: {src}\n{e}")
        dst.mkdir(parents=True, exist_ok=True)
        if src and dst:
            error_log = ErrorLog(dst)
            error_log.write(cmd, str(e))
        return None


def format_timedelta(timedelta) -> str:
    """
    Formats a timedelta object into HH:MM:SS.

    :param timedelta: The timedelta object.
    :return: A string in HH:MM:SS format.
    """
    total_seconds = int(timedelta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def formatted_size(size: int) -> str:
    """
    Converts a size in bytes to a human-readable format.

    :param size: Size in bytes.
    :return: Formatted string with appropriate unit (B, KB, MB, GB, TB).
    """
    for unit, threshold in [
        ("B", 1024),
        ("KB", 1024**2),
        ("MB", 1024**3),
        ("GB", 1024**4),
        ("TB", 1024**5),
    ]:
        if size < threshold:
            return f"{size // (threshold // 1024):,} {unit}"
    return f"{size:,} B"


def contains_any_extensions(extensions: List[str], path: Path) -> bool:
    """
    Checks if the file path contains any of the specified extensions.

    :param extensions: List of file extensions to check for.
    :param path: Path object from which the filename is extracted.
    :return: True if any extension is found in the filename, False otherwise.
    """
    filename_lower = path.name.lower()
    return any(ext.lower() in filename_lower for ext in extensions)


def detect_audio_language_multi_segments(
    in_file: Path,
    stream: dict,
    segments: int = 0,
    duration: int = 0,
    temp_dir: Path = Path(tempfile.gettempdir()),
) -> str:
    """
    Detects the language of an audio file by analyzing multiple segments.

    :param in_file: Path to the input audio file.
    :param stream: Dictionary with stream information.
    :param segments: Number of segments to use for detection.
    :param duration: Duration of the audio stream.
    :param temp_dir: Directory for temporary files.
    :return: Most common language detected.
    """
    stream_duration = duration or int(float(stream.get("duration", 0)))

    audio_duration = 30  # seconds per segment
    start_skip = 120  # seconds to skip at start
    max_segment = 5

    if stream_duration < audio_duration * 2 + start_skip:
        return LANGUAGE_WORDS[0]

    if segments == 0:  # auto-detect number of segments
        buffer = 3  # seconds buffer between segments
        segments = int(
            min(
                (stream_duration - start_skip) // (audio_duration + buffer), max_segment
            )
        )

    language_list = [
        detect_audio_language_single(
            in_file,
            stream,
            start_skip
            + int((stream_duration - start_skip - audio_duration) * i) // segments,
            duration=audio_duration,
            temp_dir=temp_dir,
        )
        for i in range(1, segments + 1)
    ]

    most_common_lang = collections.Counter(language_list).most_common(1)[0][0]
    return most_common_lang


def detect_audio_language_single(
    in_file: Path,
    stream: dict,
    start_second: int,
    duration: int,
    temp_dir: Path = Path(tempfile.gettempdir()),
) -> str:
    """
    Detects the language of a single audio segment using Whisper.

    :param in_file: Path to the input audio file.
    :param stream: Dictionary with stream information.
    :param start_second: Start time in seconds for the segment.
    :param duration: Duration of the segment in seconds.
    :param temp_dir: Directory for temporary files.
    :return: Detected language code (e.g., 'jp').
    """
    map_index = int(stream.get("index", 0))

    try:
        with tempfile.TemporaryDirectory(
            prefix=".temp_detect_language_", dir=temp_dir
        ) as temp_dir_path:
            audio_file = temp_dir_path / Path(f"{in_file.name}.mp3")
            max_bitrate = 192 * 1000
            abitrate = min(max_bitrate, int(float(stream.get("bit_rate", max_bitrate))))

            cmd = (
                f'ffmpeg -y -ss {int(start_second)} -t {int(duration)} -i "{in_file}" '
                f'-c:a libmp3lame -b:a {abitrate} -map 0:{map_index} "{audio_file}"'
            )

            res = run_cmd(cmd)
            if not res or res.returncode != 0:
                logger.error(
                    f"Error generating audio file: {in_file}, return code: {res.returncode if res else 'N/A'}"
                )
                return LANGUAGE_WORDS[0]  # fallback to default language code

            model_size = "large-v3"
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            segments, info = model.transcribe(str(audio_file), beam_size=5)
            return info.language
    except Exception as e:
        logger.error(f"Failed to detect language for file {in_file}: {e}")
        return LANGUAGE_WORDS[0]

def get_args():
    """
    Parses command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Smart Encoder for video files.")
    parser.add_argument(
        "--processes", type=int, default=4, help="Number of processes to use."
    )
    parser.add_argument(
        "--random", action="store_true", help="encode files in random order."
    )
    parser.add_argument(
        "--not-rename", action="store_true", help="Do not rename files after encoding."
    )
    parser.add_argument(
        "--audio-only", action="store_true", help="Process only audio files."
    )
    parser.add_argument(
        "--move-raw-file", action="store_true", help="Move raw files after processing."
    )
    parser.add_argument(
        "--manual-mode",
        action="store_true",
        help="Run in manual mode with fixed paths.",
    )
    parser.add_argument(
        "--av1-only", action="store_true", help="Encode using AV1 codec only."
    )
    parser.add_argument(
        "--keep-mtime", action="store_true", help="Encode using AV1 codec only."
    )
    return parser.parse_args()
