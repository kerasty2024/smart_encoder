import argparse
from pathlib import Path # 追加

def get_args() -> argparse.Namespace:
    """
    Parses command-line arguments for the Smart Encoder.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(description="Smart Encoder for video/audio files.")
    parser.add_argument(
        "--processes", type=int, default=4, help="Number of processes to use."
    )
    parser.add_argument(
        "--random", action="store_true", help="Encode files in random order."
    )
    parser.add_argument(
        "--not-rename", action="store_true", help="Do not rename files after encoding."
    )
    parser.add_argument(
        "--audio-only", action="store_true", help="Process only audio files (primarily for iPhone pipeline)."
    )
    parser.add_argument(
        "--move-raw-file", action="store_true", help="Move raw files after processing."
    )
    parser.add_argument(
        "--manual-mode",
        action="store_true",
        help="Run in manual mode with fixed paths (affects PreVideoEncoder).",
    )
    parser.add_argument(
        "--av1-only", action="store_true", help="Encode using AV1 codec only (currently, SVT-AV1 is default)."
    )
    parser.add_argument(
        "--keep-mtime", action="store_true", help="Keep original modification time for encoded files."
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Set the logging level."
    )
    parser.add_argument(
        "--allow-no-audio", action="store_true",
        help="Allow encoding video files even if no suitable audio stream is found (encodes video without audio)."
    )
    parser.add_argument(
        "--temp-work-dir", type=str, default=None, # New argument
        help="Specify a directory for temporary files. Useful for pointing to a RAM disk to reduce HDD/SSD writes."
    )
    # Hypothetical arguments to manage different pipelines or debug modes from __main__.py
    parser.add_argument(
        "--iphone-specific-task", action="store_true",
        help="Run the encoding pipeline tailored for iPhone (uses PhoneEncodingPipeline)."
    )
    parser.add_argument(
        "--debug-iphone-mode", action="store_true",
        help="Run iPhone pipeline in a special debug configuration (overrides some args)."
    )
    parser.add_argument(
        "--target-dir", type=str, default=None,
        help="Target directory for processing, used in debug modes or specific tasks."
    )

    args = parser.parse_args()

    # Validate temp_work_dir if provided
    if args.temp_work_dir:
        temp_dir_path = Path(args.temp_work_dir)
        if not temp_dir_path.is_dir():
            # Try to create it if it doesn't exist
            try:
                temp_dir_path.mkdir(parents=True, exist_ok=True)
                print(f"INFO: Created temporary working directory: {temp_dir_path}")
            except Exception as e:
                parser.error(f"The specified temporary working directory '{args.temp_work_dir}' is not a valid directory and could not be created: {e}")
        args.temp_work_dir = temp_dir_path.resolve() # Store as resolved Path object

    return args