"""
This module provides utility functions related to FFmpeg and other external tools.
It includes a robust function for running command-line processes and advanced
functionality for audio language detection using the Whisper model.
"""

import collections
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any, Union, Tuple
import shlex
import os
import re

from faster_whisper import WhisperModel
from loguru import logger

from ..services.logging_service import ErrorLog
from ..config.common import LANGUAGE_WORDS


def run_cmd(
    cmd_parts: Union[str, List[str]],
    src_file_for_log: Path = Path(),
    error_log_dir_for_run_cmd: Optional[Path] = None,
    show_cmd: bool = False,
    cmd_log_file_path: Optional[Path] = None,
) -> Optional[subprocess.CompletedProcess]:
    """
    Executes an external command safely and captures its output.

    This is a wrapper around Python's `subprocess.run` that adds enhanced logging,
    error handling, and flexibility. It can accept a command as either a single
    string or a list of arguments.

    Args:
        cmd_parts: The command to execute, as a single string or a list of strings.
                   A list is preferred for safety (avoids shell injection).
        src_file_for_log: The source file being processed, used for logging context
                          in case of an error.
        error_log_dir_for_run_cmd: The directory where an error log should be written
                                   if the command fails.
        show_cmd: If True, the command will be logged at the DEBUG level before execution.
        cmd_log_file_path: If provided, the executed command string will be appended
                           to this file.

    Returns:
        A `subprocess.CompletedProcess` object on success, containing the return code,
        stdout, and stderr. Returns `None` if the command fails to start (e.g.,
        `FileNotFoundError`).
    """
    cmd_list: List[str]

    # --- Step 1: Normalize the input command to a list of strings ---
    if isinstance(cmd_parts, str):
        # If a single string is provided, use shlex to split it safely.
        logger.warning(
            f"run_cmd received a command string, attempting to split with shlex: {cmd_parts[:100]}..."
        )
        try:
            cmd_list = shlex.split(cmd_parts)
        except ValueError as e:
            logger.error(
                f"Error splitting command string with shlex: '{cmd_parts}'. Error: {e}"
            )
            return None
    elif isinstance(cmd_parts, list):
        cmd_list = cmd_parts
    else:
        logger.error(
            f"run_cmd expects a command string or list, but received {type(cmd_parts)}."
        )
        return None

    if not cmd_list:
        logger.error("run_cmd received an empty command list.")
        return None

    # --- Step 2: Create a display-friendly version of the command for logging ---
    try:
        # Use platform-specific methods to correctly quote and join the command.
        if os.name == "nt":
            display_cmd_str = subprocess.list2cmdline(cmd_list)
        else:
            # shlex.join is available in Python 3.8+
            if hasattr(shlex, "join"):
                display_cmd_str = shlex.join(cmd_list)
            else:  # Fallback for older Python versions
                display_cmd_str = " ".join(shlex.quote(s) for s in cmd_list)
    except Exception as e:
        logger.warning(
            f"Could not format command list for display: {e}. Using simple join."
        )
        display_cmd_str = " ".join(cmd_list)

    if show_cmd:
        logger.debug(f"Executing command list: {cmd_list}")
        logger.debug(f"Formatted command for display/logging: {display_cmd_str}")

    # --- Step 3: Log the command to a file if requested ---
    if cmd_log_file_path:
        try:
            cmd_log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with cmd_log_file_path.open("a", encoding="utf-8") as cmd_f:
                cmd_f.write(display_cmd_str + "\n")
        except Exception as e:
            logger.error(
                f"Failed to write command to log file {cmd_log_file_path}: {e}"
            )

    # --- Step 4: Execute the command and handle potential errors ---
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,  # Capture stdout and stderr.
            text=True,  # Decode stdout/stderr as text.
            encoding="utf-8",
            shell=False,  # Never use shell=True with untrusted input.
        )

        # Log output for debugging purposes.
        if result.stdout and len(result.stdout) > 500:
            logger.trace(f"Command stdout (truncated): {result.stdout[:500]}...")
        elif result.stdout:
            logger.trace(f"Command stdout: {result.stdout}")

        # Distinguish between error output and informational warnings on stderr.
        if result.stderr and result.returncode != 0:
            logger.debug(
                f"Command stderr (error, rc={result.returncode}): {result.stderr}"
            )
        elif result.stderr:
            logger.trace(
                f"Command stderr (non-error, rc={result.returncode}): {result.stderr}"
            )

        return result
    except FileNotFoundError:
        # This error occurs if the executable (e.g., 'ffmpeg') is not found.
        logger.error(
            f"Error: Command not found (e.g., '{cmd_list[0]}'). Ensure it's in your system's PATH or configured correctly."
        )
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            ErrorLog(error_log_dir_for_run_cmd).write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                "Error: Command not found (FileNotFoundError).",
            )
        return None
    except subprocess.TimeoutExpired:
        # This error occurs if the command takes too long (though no timeout is set here, it's good practice).
        logger.error(f"Error: Command timed out. Command: {display_cmd_str}")
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            ErrorLog(error_log_dir_for_run_cmd).write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                "Error: Command timed out.",
            )
        return None
    except Exception as e:
        # Catch any other unexpected exceptions during subprocess execution.
        logger.error(
            f"An unexpected error occurred while executing command for {src_file_for_log.name if src_file_for_log else 'N/A'}: {e}",
            exc_info=True,
        )
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            ErrorLog(error_log_dir_for_run_cmd).write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                f"Exception: {type(e).__name__} - {e}",
            )
        return None


# --- Whisper Parameter Configuration ---
# Default settings for systems with high VRAM (>10GB).
WHISPER_PARAMS_HIGH_VRAM = {
    "whisper_model_size": "large-v3",
    "whisper_device": "cuda",
    "whisper_compute_type": "float16",
}
# Default settings for systems with lower VRAM (<=10GB) but still using CUDA.
WHISPER_PARAMS_LOW_VRAM_CUDA = {
    "whisper_model_size": "medium",
    "whisper_device": "cuda",
    "whisper_compute_type": "float32",
}
# Fallback settings for systems without a compatible GPU (CPU-only).
WHISPER_PARAMS_CPU = {
    "whisper_model_size": "base",
    "whisper_device": "cpu",
    "whisper_compute_type": "int8",
}

# This global variable will cache the selected parameters to avoid re-checking the hardware.
_whisper_params_to_use: Optional[Dict[str, str]] = None


def _get_gpu_info_nvidia_smi() -> Tuple[Optional[float], Optional[str]]:
    """
    Uses the `nvidia-smi` command to get VRAM (in GB) and the GPU name.

    Returns:
        A tuple containing (vram_gb, gpu_name). Returns (None, None) if `nvidia-smi`
        fails or is not found.
    """
    try:
        # Get total VRAM in MiB.
        result_vram = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        vram_mb_str = result_vram.stdout.strip().split("\n")[0]
        vram_gb = int(vram_mb_str) / 1024

        # Get the GPU name.
        result_name = subprocess.run(
            ["nvidia-smi", "--query-gpu=gpu_name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            encoding="utf-8",
        )
        gpu_name = result_name.stdout.strip().split("\n")[0].lower()

        logger.info(
            f"nvidia-smi check successful: VRAM {vram_gb:.2f} GB, GPU Name: {gpu_name}"
        )
        return vram_gb, gpu_name
    except (
        FileNotFoundError,
        subprocess.CalledProcessError,
        ValueError,
        IndexError,
        UnicodeDecodeError,
    ) as e:
        if isinstance(e, FileNotFoundError):
            logger.info(
                "nvidia-smi command not found. Assuming no NVIDIA GPU or it's not in the system's PATH."
            )
        else:
            logger.warning(
                f"Could not get GPU info via nvidia-smi. This is expected on non-NVIDIA systems. Error: {e}"
            )
        return None, None


def get_whisper_params() -> Dict[str, str]:
    """
    Determines the optimal Whisper model parameters based on available hardware.

    It checks the system's VRAM using `nvidia-smi` and selects a model size and
    compute type accordingly. If no compatible GPU is found, it falls back to
    CPU-based parameters. The result is cached for subsequent calls.

    Returns:
        A dictionary containing the selected 'whisper_model_size', 'whisper_device',
        and 'whisper_compute_type'.
    """
    global _whisper_params_to_use
    if _whisper_params_to_use is None:
        vram_gb, gpu_name = _get_gpu_info_nvidia_smi()

        if vram_gb is not None and gpu_name is not None:
            # NVIDIA GPU detected.
            if vram_gb > 10:
                _whisper_params_to_use = WHISPER_PARAMS_HIGH_VRAM.copy()
            else:
                _whisper_params_to_use = WHISPER_PARAMS_LOW_VRAM_CUDA.copy()
        else:
            # `nvidia-smi` failed, so fall back to CPU.
            logger.warning(
                "Failed to get NVIDIA GPU info. Falling back to CPU for Whisper language detection."
            )
            _whisper_params_to_use = WHISPER_PARAMS_CPU.copy()

        logger.info(f"Whisper parameters have been selected: {_whisper_params_to_use}")
    return _whisper_params_to_use


def detect_audio_language_single(
    input_media_file: Path,
    audio_stream_info: Dict,
    start_time_seconds: int,
    segment_duration_seconds: int,
    temp_work_dir_override: Optional[Path] = None,
) -> str:
    """
    Detects the language of a single audio segment using Whisper.

    This function extracts a short audio clip from the media file, transcribes it
    using the Whisper model, and returns the detected language code.

    Args:
        input_media_file: The path to the source media file.
        audio_stream_info: A dictionary of metadata for the audio stream to analyze.
        start_time_seconds: The start time (in seconds) of the segment to extract.
        segment_duration_seconds: The duration (in seconds) of the segment.
        temp_work_dir_override: An optional path to a directory for temporary files.

    Returns:
        The detected language code (e.g., "en", "ja"), or a default code if
        detection fails.
    """
    params = get_whisper_params()
    default_language_code = LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"
    stream_index = audio_stream_info.get("index")
    if stream_index is None:
        logger.error("Audio stream 'index' not found. Cannot detect language.")
        return default_language_code

    effective_temp_dir = (
        temp_work_dir_override
        if temp_work_dir_override and temp_work_dir_override.is_dir()
        else None
    )

    try:
        # Create a temporary directory to store the audio segment.
        with tempfile.TemporaryDirectory(
            prefix=".temp_detect_lang_", dir=effective_temp_dir
        ) as temp_dir_str:
            temp_segment_path = Path(temp_dir_str)
            temp_audio_file = temp_segment_path / f"{input_media_file.stem}_segment.mp3"

            # --- Step 1: Extract the audio segment using FFmpeg ---
            ffmpeg_cmd_list = [
                "ffmpeg",
                "-y",
                "-ss",
                str(start_time_seconds),
                "-t",
                str(segment_duration_seconds),
                "-i",
                str(input_media_file.resolve()),
                "-map",
                f"0:{stream_index}",
                "-c:a",
                "libmp3lame",
                "-b:a",
                "192k",
                "-ar",
                "16000",
                "-ac",
                "1",
                str(temp_audio_file.resolve()),
            ]
            res = run_cmd(
                ffmpeg_cmd_list, src_file_for_log=input_media_file, show_cmd=__debug__
            )

            if not res or res.returncode != 0:
                logger.error(
                    f"Error extracting audio segment for language detection from {input_media_file.name}. FFmpeg stderr: {res.stderr if res else 'N/A'}"
                )
                return default_language_code
            if not temp_audio_file.exists() or temp_audio_file.stat().st_size == 0:
                logger.error(f"Extracted audio segment is empty or missing.")
                return default_language_code

            # --- Step 2: Use Whisper to transcribe the segment and get the language ---
            model = None
            try:
                # Loading the model can be slow and resource-intensive.
                model = WhisperModel(
                    params["whisper_model_size"],
                    device=params["whisper_device"],
                    compute_type=params["whisper_compute_type"],
                )
                segments_iterable, lang_info = model.transcribe(
                    str(temp_audio_file), beam_size=5
                )
                detected_lang_code = lang_info.language
                logger.debug(
                    f"Detected language for segment: {detected_lang_code} (Probability: {lang_info.language_probability:.2f})"
                )
                return detected_lang_code
            except Exception as model_ex:
                logger.error(
                    f"Failed during Whisper model processing: {model_ex}. Check your setup (e.g., CUDA/cuDNN)."
                )
                return default_language_code
            finally:
                # Attempt to release model resources, though this can be tricky.
                if model is not None:
                    del model
    except Exception as e:
        logger.error(
            f"General failure during language detection for {input_media_file.name}: {e}",
            exc_info=True,
        )
        return default_language_code


def detect_audio_language_multi_segments(
    input_media_file: Path,
    audio_stream_info: Dict,
    num_segments_to_check: int = 0,
    total_media_duration_seconds: int = 0,
    temp_work_dir_override: Optional[Path] = None,
) -> str:
    """
    Detects the dominant language by analyzing multiple segments of an audio stream.

    To improve accuracy, this function analyzes several short clips from different
    parts of the audio stream and returns the most commonly detected language.

    Args:
        input_media_file: The path to the source media file.
        audio_stream_info: Metadata for the audio stream to analyze.
        num_segments_to_check: The number of segments to check. If 0, it's determined automatically.
        total_media_duration_seconds: The total duration of the media.
        temp_work_dir_override: An optional path to a directory for temporary files.

    Returns:
        The most common language code detected across all segments.
    """
    default_language_code = LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"
    stream_duration_sec = total_media_duration_seconds or int(
        float(audio_stream_info.get("duration", 0))
    )

    # If the file is too short, just analyze one central segment.
    if stream_duration_sec < 180:
        logger.debug(
            f"Audio duration ({stream_duration_sec}s) is short. Analyzing one central segment."
        )
        start_offset = max(0, (stream_duration_sec - 30) // 2)
        return detect_audio_language_single(
            input_media_file,
            audio_stream_info,
            start_offset,
            30,
            temp_work_dir_override=temp_work_dir_override,
        )

    # Automatically determine how many segments to check based on duration.
    if num_segments_to_check == 0:
        effective_num_segments = max(1, min(int((stream_duration_sec - 60) / 40), 3))
    else:
        effective_num_segments = num_segments_to_check

    logger.debug(
        f"Analyzing {effective_num_segments} segments for language in {input_media_file.name}."
    )
    detected_languages: List[str] = []

    # Analyze segments spread across the media file.
    for i in range(effective_num_segments):
        # Calculate start time to spread segments evenly.
        progress = (
            i / (effective_num_segments - 1) if effective_num_segments > 1 else 0.5
        )
        start_time = 60 + int((stream_duration_sec - 90) * progress)
        lang_code = detect_audio_language_single(
            input_media_file,
            audio_stream_info,
            start_time,
            30,
            temp_work_dir_override=temp_work_dir_override,
        )
        detected_languages.append(lang_code)

    if not detected_languages:
        return default_language_code

    # Return the most frequently detected language.
    language_counts = collections.Counter(detected_languages)
    most_common_lang, count = language_counts.most_common(1)[0]
    logger.info(f"Most common language detected: {most_common_lang} ({count}/{len(detected_languages)} segments). All detections: {dict(language_counts)}")
    return most_common_lang