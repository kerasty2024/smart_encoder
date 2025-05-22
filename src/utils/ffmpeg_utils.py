import collections
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Dict  # Added Dict for type hint

from faster_whisper import WhisperModel  # Assuming this is a direct dependency
from loguru import logger

# Assuming ErrorLog is now in services.logging_service
# This creates a utils -> services dependency.
# Ideally, run_cmd should return error details or raise an exception,
# and logging should be handled by the caller in the services layer.
# For minimal changes, we keep the direct ErrorLog usage.
from ..services.logging_service import ErrorLog
from ..config.common import LANGUAGE_WORDS  # For language detection defaults


def run_cmd(
    cmd_str: str,  # Renamed from cmd to cmd_str for clarity
    src_file_for_log: Path = Path(),  # Context for logging errors
    error_log_dir_for_run_cmd: Optional[
        Path
    ] = None,  # Directory to write error log if command fails
    show_cmd: bool = False,  # If True, logs the command before execution
    cmd_log_file_path: Optional[
        Path
    ] = None,  # If provided, appends command to this file
) -> Optional[subprocess.CompletedProcess]:
    """
    Executes a shell command using subprocess.run() and logs the output.

    Args:
        cmd_str: The command string to execute.
        src_file_for_log: Path to the source file, used for context in error logs.
        error_log_dir_for_run_cmd: Directory where an error log (e.g., error.txt) will be written by ErrorLog class if command fails.
        show_cmd: If True, logs the command string before execution.
        cmd_log_file_path: If provided, appends the executed command string to this file.

    Returns:
        subprocess.CompletedProcess object if successful, or None if an exception occurs
        during subprocess.run() itself (not for non-zero return codes).
    """
    if show_cmd:
        logger.debug(f"Executing command: {cmd_str}")

    if cmd_log_file_path:
        try:
            cmd_log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with cmd_log_file_path.open("a", encoding="utf-8") as cmd_f:
                cmd_f.write(cmd_str + "\n")  # Add newline for readability
        except Exception as e:
            logger.error(
                f"Failed to write command to log file {cmd_log_file_path}: {e}"
            )

    try:
        # Using shell=False is generally safer if cmd_str is complex or contains user input.
        # If cmd_str is a simple command string without shell metacharacters, shell=False with shlex.split(cmd_str) is best.
        # However, the original code implies cmd_str might be a full shell command.
        # For now, keep consistent with implicit shell=False behavior of passing a string directly on Windows,
        # or requiring shlex.split on Linux if shell=False.
        # To be safe and cross-platform with string commands that might have spaces in paths:
        # result = subprocess.run(shlex.split(cmd_str), capture_output=True, text=True, encoding="utf-8", check=False)
        # But since ffmpeg paths are quoted in cmd_str, direct string might work on Windows.
        # For simplicity, let's assume the command string is crafted to work as-is.
        # `text=True` implies `universal_newlines=True`. `encoding` for decoding stdout/stderr.
        # `check=False` (default) means it won't raise CalledProcessError for non-zero exit codes.
        result = subprocess.run(
            cmd_str, capture_output=True, text=True, encoding="utf-8", shell=True
        )  # shell=True if cmd_str is complex
        # Log stdout/stderr if they are substantial, for debugging, even on success
        if result.stdout and len(result.stdout) > 100:  # Avoid logging trivial output
            logger.trace(f"Command stdout (truncated): {result.stdout[:500]}")
        if result.stderr and result.returncode != 0:  # Always log stderr on error
            logger.warning(f"Command stderr: {result.stderr}")
        elif result.stderr:  # Log non-error stderr as trace
            logger.trace(f"Command stderr (non-error): {result.stderr}")

        return result

    except FileNotFoundError:  # e.g. ffmpeg not found in PATH
        logger.error(
            f"Error: Command not found (e.g., ffmpeg). Ensure it's in your PATH. Command: {cmd_str[:100]}..."
        )
        if (
            error_log_dir_for_run_cmd and src_file_for_log.name
        ):  # Check if src_file_for_log is meaningful
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {cmd_str}",
                "Error: Command not found (FileNotFoundError). Check PATH.",
            )
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"Error: Command timed out. Command: {cmd_str[:100]}...")
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {cmd_str}",
                "Error: Command timed out (TimeoutExpired).",
            )
        return None
    except Exception as e:  # Catch other potential subprocess errors
        logger.error(
            f"Error executing command for {src_file_for_log.name if src_file_for_log else 'N/A'}: {cmd_str[:100]}...\nException: {e}"
        )
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {cmd_str}",
                f"Exception: {type(e).__name__} - {e}",
            )
        return None


def detect_audio_language_single(
    input_media_file: Path,
    audio_stream_info: Dict,  # FFmpeg stream dict for the specific audio stream
    start_time_seconds: int,
    segment_duration_seconds: int,
    temp_processing_dir: Path = Path(tempfile.gettempdir()),
    whisper_model_size: str = "large-v3",  # Or "base", "small", etc.
    whisper_device: str = "cuda",  # "cpu" or "cuda"
    whisper_compute_type: str = "float16",  # "int8", "float16", "float32"
) -> str:
    """
    Detects the language of a single audio segment using Whisper.

    Args:
        input_media_file: Path to the input audio file.
        audio_stream_info: Dictionary with stream information for the target audio stream.
        start_time_seconds: Start time in seconds for the segment to analyze.
        segment_duration_seconds: Duration of the segment in seconds.
        temp_processing_dir: Directory for temporary audio segment files.
        whisper_model_size, whisper_device, whisper_compute_type: Whisper model parameters.

    Returns:
        Detected language code (e.g., 'ja', 'en') or default if detection fails.
    """
    default_language_code = (
        LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"
    )  # Fallback language

    stream_index = audio_stream_info.get("index")
    if stream_index is None:
        logger.error(
            "Audio stream 'index' not found in audio_stream_info. Cannot detect language."
        )
        return default_language_code

    # Create a unique name for the temporary audio segment
    # temp_dir() context manager ensures cleanup
    try:
        with tempfile.TemporaryDirectory(
            prefix=".temp_detect_lang_", dir=temp_processing_dir
        ) as temp_segment_dir:
            temp_segment_path = Path(temp_segment_dir)
            # Suffix for temp audio file, mp3 is common for Whisper
            temp_audio_file = temp_segment_path / f"{input_media_file.stem}_segment.mp3"

            # Determine audio bitrate for the segment - use original if known and reasonable, else default
            max_segment_bitrate = 192 * 1000  # 192 kbps for mp3 segment
            original_bitrate_str = audio_stream_info.get("bit_rate")
            segment_abitrate = max_segment_bitrate  # Default
            if original_bitrate_str:
                try:
                    segment_abitrate = min(
                        int(original_bitrate_str), max_segment_bitrate
                    )
                except ValueError:
                    pass  # Keep default if original is not int

            # FFmpeg command to extract the audio segment
            # -map 0:a:{stream_index_in_input_file} or -map 0:{absolute_stream_index}
            # audio_stream_info['index'] is the absolute index from ffprobe
            ffmpeg_cmd = (
                f"ffmpeg -y -ss {start_time_seconds} -t {segment_duration_seconds} "
                f'-i "{input_media_file}" '
                f"-map 0:{stream_index} "  # Map specific audio stream by its original index
                f"-c:a libmp3lame -b:a {segment_abitrate} -ar 16000 -ac 1 "  # Force 16kHz mono for Whisper
                f'"{temp_audio_file}"'
            )

            res = run_cmd(
                ffmpeg_cmd, src_file_for_log=input_media_file, show_cmd=__debug__
            )
            if not res or res.returncode != 0:
                logger.error(
                    f"Error extracting audio segment for language detection from {input_media_file.name} "
                    f"(stream {stream_index}). FFmpeg stderr: {res.stderr if res else 'N/A'}"
                )
                return default_language_code

            if not temp_audio_file.exists() or temp_audio_file.stat().st_size == 0:
                logger.error(
                    f"Extracted audio segment {temp_audio_file.name} is empty or missing."
                )
                return default_language_code

            # Initialize Whisper model (can be slow on first run per session)
            # Model caching is handled by faster_whisper library.
            # Consider initializing model once globally if called very frequently.
            try:
                model = WhisperModel(
                    whisper_model_size,
                    device=whisper_device,
                    compute_type=whisper_compute_type,
                )
            except Exception as model_load_ex:
                logger.error(
                    f"Failed to load Whisper model ({whisper_model_size}, {whisper_device}, {whisper_compute_type}): {model_load_ex}"
                )
                logger.error(
                    "Language detection will use default. Ensure CUDA/cuDNN setup if using GPU, or sufficient RAM for CPU models."
                )
                return default_language_code

            # Transcribe and get language info
            # beam_size=5 is a common default for better accuracy.
            segments_iterable, lang_info = model.transcribe(
                str(temp_audio_file), beam_size=5
            )
            detected_lang_code = lang_info.language
            # detected_lang_probability = lang_info.language_probability # For confidence score

            logger.debug(
                f"Detected language for segment of {input_media_file.name} (stream {stream_index}): {detected_lang_code} (Prob: {lang_info.language_probability:.2f})"
            )
            return detected_lang_code

    except Exception as e:  # Catch-all for unexpected issues in this function
        logger.error(
            f"Failed during language detection for {input_media_file.name} (stream {stream_index}): {e}",
            exc_info=True,
        )
        return default_language_code


def detect_audio_language_multi_segments(
    input_media_file: Path,
    audio_stream_info: Dict,  # FFmpeg stream dict for the specific audio stream
    num_segments_to_check: int = 0,  # 0 for auto-detection
    total_media_duration_seconds: int = 0,  # Duration of the full audio stream in media_file
    temp_processing_dir: Path = Path(tempfile.gettempdir()),
) -> str:
    """
    Detects the dominant language of an audio file by analyzing multiple segments.

    Args:
        input_media_file: Path to the input audio file.
        audio_stream_info: Stream information dictionary for the target audio stream.
        num_segments_to_check: Number of segments to use for detection. 0 means auto-calculate.
        total_media_duration_seconds: Total duration of the audio stream. If 0, tries to get from stream_info.
        temp_processing_dir: Directory for temporary files.

    Returns:
        The most common language code detected across segments.
    """
    default_language_code = LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"

    # Get stream duration: from argument, or from stream_info, or default to 0
    stream_duration_sec = total_media_duration_seconds
    if not stream_duration_sec and "duration" in audio_stream_info:
        try:
            stream_duration_sec = int(float(audio_stream_info["duration"]))
        except ValueError:
            logger.warning(
                f"Invalid duration '{audio_stream_info['duration']}' in stream info for {input_media_file.name}."
            )
            stream_duration_sec = 0

    # Constants for segment analysis strategy
    min_duration_for_multi_segment = (
        180  # e.g., 3 minutes. Don't bother with multi-segment for shorter.
    )
    segment_analysis_duration_sec = 30  # Duration of each audio segment to analyze
    initial_skip_seconds = 60  # Skip first part of audio (e.g., intros)
    max_segments_auto = 3  # Max segments if num_segments_to_check is auto

    if stream_duration_sec < min_duration_for_multi_segment:
        logger.debug(
            f"Audio duration ({stream_duration_sec}s) too short for multi-segment analysis. Analyzing one central segment."
        )
        start_offset = (
            initial_skip_seconds
            if stream_duration_sec
            > initial_skip_seconds + segment_analysis_duration_sec
            else 0
        )
        # Ensure start_offset + segment_duration doesn't exceed stream_duration
        if start_offset + segment_analysis_duration_sec > stream_duration_sec:
            start_offset = max(
                0, stream_duration_sec - segment_analysis_duration_sec
            )  # Take last possible segment

        return detect_audio_language_single(
            input_media_file,
            audio_stream_info,
            start_offset,
            segment_analysis_duration_sec,
            temp_processing_dir,
        )

    # Determine number of segments if auto (num_segments_to_check == 0)
    effective_num_segments = num_segments_to_check
    if effective_num_segments == 0:
        # Calculate based on remaining duration after initial skip
        analyzable_duration = stream_duration_sec - initial_skip_seconds
        if analyzable_duration > segment_analysis_duration_sec:
            # How many full segments fit, with some buffer/spacing
            # Example: (10min_total - 1min_skip) = 9min_analyzable. 9min / (30s_segment + 10s_buffer) approx
            buffer_between_segments = 10  # seconds
            num_possible = analyzable_duration // (
                segment_analysis_duration_sec + buffer_between_segments
            )
            effective_num_segments = max(1, min(int(num_possible), max_segments_auto))
        else:  # Not enough duration even for one segment after skip
            effective_num_segments = 1
            initial_skip_seconds = 0  # Analyze from start if too short after skip

    logger.debug(
        f"Analyzing {effective_num_segments} segments for language in {input_media_file.name} (stream {audio_stream_info.get('index')})."
    )

    detected_languages_list: List[str] = []

    # Calculate start times for each segment, distributed across analyzable part
    # Analyzable part starts after initial_skip_seconds and ends before the last segment_analysis_duration_sec
    # Effective span for distributing segments: stream_duration_sec - initial_skip_seconds - segment_analysis_duration_sec
    span_for_segment_starts = (
        stream_duration_sec - initial_skip_seconds - segment_analysis_duration_sec
    )
    if span_for_segment_starts < 0:
        span_for_segment_starts = 0  # Ensure non-negative

    for i in range(effective_num_segments):
        # Distribute segment start points within the analyzable span
        # For 1 segment, it's at the start of the span.
        # For multiple, distribute them.
        if effective_num_segments == 1:
            segment_start_offset_in_span = 0
        else:
            # Distribute i from 0 to N-1 across the span_for_segment_starts
            segment_start_offset_in_span = (
                int((span_for_segment_starts * i) / (effective_num_segments - 1))
                if effective_num_segments > 1
                else 0
            )

        actual_start_time = initial_skip_seconds + segment_start_offset_in_span

        # Ensure actual_start_time + segment_duration doesn't exceed total stream duration
        if actual_start_time + segment_analysis_duration_sec > stream_duration_sec:
            actual_start_time = max(
                0, stream_duration_sec - segment_analysis_duration_sec
            )  # Take last possible full segment

        lang_code = detect_audio_language_single(
            input_media_file,
            audio_stream_info,
            actual_start_time,
            segment_analysis_duration_sec,
            temp_processing_dir,
        )
        if (
            lang_code != default_language_code
        ):  # Optionally, only add if not default (more robust for mixed content)
            detected_languages_list.append(lang_code)
        elif (
            not detected_languages_list
        ):  # If all are default, add at least one default
            detected_languages_list.append(default_language_code)

    if not detected_languages_list:  # Should not happen if default is added
        logger.warning(
            f"No languages detected for {input_media_file.name}. Returning default."
        )
        return default_language_code

    # Find the most common language
    language_counts = collections.Counter(detected_languages_list)
    most_common_lang, count = language_counts.most_common(1)[0]

    logger.info(f"Most common language for {input_media_file.name} (stream {audio_stream_info.get('index')}): {most_common_lang} (Count: {count} of {len(detected_languages_list)}). All detected: {language_counts}")
    return most_common_lang