import argparse

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
        "--allow-no-audio", action="store_true", # New argument
        help="Allow encoding video files even if no suitable audio stream is found (encodes video without audio)."
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
    return args