import collections
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any, Union # Union を追加
import shlex
import os # os をインポート

from faster_whisper import WhisperModel
from loguru import logger

from ..services.logging_service import ErrorLog # 相対インポートに修正
from ..config.common import LANGUAGE_WORDS # 相対インポートに修正


def run_cmd(
    cmd_parts: Union[str, List[str]], # コマンドを文字列またはリストで受け付ける
    src_file_for_log: Path = Path(),
    error_log_dir_for_run_cmd: Optional[Path] = None,
    show_cmd: bool = False,
    cmd_log_file_path: Optional[Path] = None,
) -> Optional[subprocess.CompletedProcess]:

    cmd_list: List[str]
    display_cmd_str: str

    if isinstance(cmd_parts, str):
        # 文字列で渡された場合、shlex.splitでリストに変換 (主に古い呼び出し箇所のため)
        # Windowsではshlex.splitはバックスラッシュの扱いに注意。
        # 確実なのは呼び出し元でリストを渡すこと。
        logger.warning(f"run_cmd received a command string, attempting to split with shlex: {cmd_parts[:100]}...")
        try:
            cmd_list = shlex.split(cmd_parts)
        except ValueError as e:
            logger.error(f"Error splitting command string with shlex: '{cmd_parts}'. Error: {e}")
            return None
    elif isinstance(cmd_parts, list):
        cmd_list = cmd_parts
    else:
        logger.error(f"run_cmd expects a command string or list, got {type(cmd_parts)}")
        return None

    if not cmd_list:
        logger.error("run_cmd received an empty command list.")
        return None

    # 表示用コマンド文字列の生成
    try:
        # subprocess.list2cmdline は Windows 専用で、引数を適切にエスケープして
        # cmd.exe で実行可能な形式の文字列を生成する。
        # POSIX 環境では shlex.join (Python 3.8+) または手動でクォートする。
        if os.name == 'nt':
            display_cmd_str = subprocess.list2cmdline(cmd_list)
        else:
            if hasattr(shlex, 'join'): # Python 3.8+
                display_cmd_str = shlex.join(cmd_list)
            else:
                display_cmd_str = " ".join(shlex.quote(s) for s in cmd_list)
    except Exception as e:
        logger.warning(f"Could not format command list for display: {e}. Using simple join.")
        display_cmd_str = " ".join(cmd_list)


    if show_cmd:
        logger.debug(f"Executing command list: {cmd_list}")
        logger.debug(f"Formatted command for display/logging: {display_cmd_str}")


    if cmd_log_file_path:
        try:
            cmd_log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with cmd_log_file_path.open("a", encoding="utf-8") as cmd_f:
                cmd_f.write(display_cmd_str + "\n") # ログファイルには整形された文字列を記録
        except Exception as e:
            logger.error(
                f"Failed to write command to log file {cmd_log_file_path}: {e}"
            )

    try:
        result = subprocess.run(
            cmd_list, # リスト形式でコマンドを渡す
            capture_output=True,
            text=True,
            encoding="utf-8", # stderr/stdoutのデコードエンコーディング
            shell=False #シェルを介さずに直接実行
        )
        if result.stdout and len(result.stdout) > 500: # 長すぎる場合は切り詰める
            logger.trace(f"Command stdout (truncated): {result.stdout[:500]}...")
        elif result.stdout:
            logger.trace(f"Command stdout: {result.stdout}")

        if result.stderr and result.returncode != 0:
            logger.debug(f"Command stderr (error, rc={result.returncode}): {result.stderr}")
        elif result.stderr:
            logger.trace(f"Command stderr (non-error, rc={result.returncode}): {result.stderr}")
        return result
    except FileNotFoundError:
        logger.error(
            f"Error: Command not found (e.g., '{cmd_list[0]}'). Ensure it's in your PATH. Full command: {display_cmd_str}"
        )
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                "Error: Command not found (FileNotFoundError). Check PATH.",
            )
        return None
    except subprocess.TimeoutExpired:
        logger.error(f"Error: Command timed out. Command: {display_cmd_str}")
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                "Error: Command timed out (TimeoutExpired).",
            )
        return None
    except Exception as e:
        logger.error(
            f"Error executing command for {src_file_for_log.name if src_file_for_log else 'N/A'}: {display_cmd_str}\nException: {e}",
            exc_info=True # トレースバックも出力
        )
        if error_log_dir_for_run_cmd and src_file_for_log.name:
            error_log_instance = ErrorLog(error_log_dir_for_run_cmd)
            error_log_instance.write(
                f"Command execution error for: {src_file_for_log.name}",
                f"Command: {display_cmd_str}",
                f"Exception: {type(e).__name__} - {e}",
            )
        return None


def detect_audio_language_single(
    input_media_file: Path,
    audio_stream_info: Dict,
    start_time_seconds: int,
    segment_duration_seconds: int,
    temp_work_dir_override: Optional[Path] = None,
    whisper_model_size: str = "large-v3",
    whisper_device: str = "cuda",
    whisper_compute_type: str = "float16",
) -> str:
    default_language_code = LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"
    stream_index = audio_stream_info.get("index")
    if stream_index is None:
        logger.error("Audio stream 'index' not found in audio_stream_info. Cannot detect language.")
        return default_language_code

    effective_temp_dir = None
    if temp_work_dir_override and temp_work_dir_override.is_dir():
        effective_temp_dir = temp_work_dir_override
        logger.debug(f"Using specified temporary working directory for language detection segment: {effective_temp_dir}")
    else:
        logger.debug("Using system default temporary directory for language detection segment.")

    try:
        with tempfile.TemporaryDirectory(prefix=".temp_detect_lang_", dir=effective_temp_dir) as temp_segment_dir_str:
            temp_segment_path = Path(temp_segment_dir_str)
            temp_audio_file = temp_segment_path / f"{input_media_file.stem}_segment.mp3"
            max_segment_bitrate = 192 * 1000
            original_bitrate_str = audio_stream_info.get("bit_rate")
            segment_abitrate = max_segment_bitrate
            if original_bitrate_str:
                try:
                    segment_abitrate = min(int(original_bitrate_str), max_segment_bitrate)
                except ValueError:
                    pass

            ffmpeg_cmd_list = [
                "ffmpeg", "-y",
                "-ss", str(start_time_seconds),
                "-t", str(segment_duration_seconds),
                "-i", str(input_media_file.resolve()), # Ensure absolute path
                "-map", f"0:{stream_index}",
                "-c:a", "libmp3lame",
                "-b:a", str(segment_abitrate),
                "-ar", "16000", "-ac", "1",
                str(temp_audio_file.resolve()) # Ensure absolute path
            ]
            res = run_cmd(ffmpeg_cmd_list, src_file_for_log=input_media_file, show_cmd=__debug__)

            if not res or res.returncode != 0:
                logger.error(
                    f"Error extracting audio segment for language detection from {input_media_file.name} "
                    f"(stream {stream_index}). FFmpeg stderr: {res.stderr if res else 'N/A'}"
                )
                return default_language_code
            if not temp_audio_file.exists() or temp_audio_file.stat().st_size == 0:
                logger.error(f"Extracted audio segment {temp_audio_file.name} is empty or missing.")
                return default_language_code

            try:
                model = WhisperModel(whisper_model_size, device=whisper_device, compute_type=whisper_compute_type)
            except Exception as model_load_ex:
                logger.error(f"Failed to load Whisper model ({whisper_model_size}, {whisper_device}, {whisper_compute_type}): {model_load_ex}")
                logger.error("Language detection will use default. Ensure CUDA/cuDNN setup if using GPU, or sufficient RAM for CPU models.")
                return default_language_code

            segments_iterable, lang_info = model.transcribe(str(temp_audio_file), beam_size=5)
            detected_lang_code = lang_info.language
            logger.debug(f"Detected language for segment of {input_media_file.name} (stream {stream_index}): {detected_lang_code} (Prob: {lang_info.language_probability:.2f})")
            return detected_lang_code
    except Exception as e:
        logger.error(
            f"Failed during language detection for {input_media_file.name} (stream {stream_index}): {e}",
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
    default_language_code = LANGUAGE_WORDS[0] if LANGUAGE_WORDS else "und"
    stream_duration_sec = total_media_duration_seconds
    if not stream_duration_sec and "duration" in audio_stream_info:
        try:
            stream_duration_sec = int(float(audio_stream_info["duration"]))
        except ValueError:
            logger.warning(f"Invalid duration '{audio_stream_info['duration']}' in stream info for {input_media_file.name}.")
            stream_duration_sec = 0

    min_duration_for_multi_segment = 180
    segment_analysis_duration_sec = 30
    initial_skip_seconds = 60
    max_segments_auto = 3

    if stream_duration_sec < min_duration_for_multi_segment:
        logger.debug(f"Audio duration ({stream_duration_sec}s) too short for multi-segment analysis. Analyzing one central segment.")
        start_offset = (
            initial_skip_seconds
            if stream_duration_sec > initial_skip_seconds + segment_analysis_duration_sec
            else 0
        )
        if start_offset + segment_analysis_duration_sec > stream_duration_sec:
            start_offset = max(0, stream_duration_sec - segment_analysis_duration_sec)
        return detect_audio_language_single(
            input_media_file, audio_stream_info, start_offset, segment_analysis_duration_sec,
            temp_work_dir_override=temp_work_dir_override
        )

    effective_num_segments = num_segments_to_check
    if effective_num_segments == 0:
        analyzable_duration = stream_duration_sec - initial_skip_seconds
        if analyzable_duration > segment_analysis_duration_sec:
            buffer_between_segments = 10
            num_possible = analyzable_duration // (segment_analysis_duration_sec + buffer_between_segments)
            effective_num_segments = max(1, min(int(num_possible), max_segments_auto))
        else:
            effective_num_segments = 1
            initial_skip_seconds = 0

    logger.debug(f"Analyzing {effective_num_segments} segments for language in {input_media_file.name} (stream {audio_stream_info.get('index')}).")
    detected_languages_list: List[str] = []
    span_for_segment_starts = stream_duration_sec - initial_skip_seconds - segment_analysis_duration_sec
    if span_for_segment_starts < 0:
        span_for_segment_starts = 0

    for i in range(effective_num_segments):
        segment_start_offset_in_span = (
            int((span_for_segment_starts * i) / (effective_num_segments - 1))
            if effective_num_segments > 1 else 0
        )
        actual_start_time = initial_skip_seconds + segment_start_offset_in_span
        if actual_start_time + segment_analysis_duration_sec > stream_duration_sec:
            actual_start_time = max(0, stream_duration_sec - segment_analysis_duration_sec)

        lang_code = detect_audio_language_single(
            input_media_file, audio_stream_info, actual_start_time, segment_analysis_duration_sec,
            temp_work_dir_override=temp_work_dir_override
        )
        if lang_code != default_language_code:
            detected_languages_list.append(lang_code)
        elif not detected_languages_list: # Keep default if no other lang detected yet
            detected_languages_list.append(default_language_code)

    if not detected_languages_list:
        logger.warning(f"No languages detected for {input_media_file.name}. Returning default.")
        return default_language_code

    language_counts = collections.Counter(detected_languages_list)
    most_common_lang, count = language_counts.most_common(1)[0]
    logger.info(
        f"Most common language for {input_media_file.name} (stream {audio_stream_info.get('index')}): {most_common_lang} "
        f"(Count: {count} of {len(detected_languages_list)}). All detected: {dict(language_counts)}"
    )
    return most_common_lang