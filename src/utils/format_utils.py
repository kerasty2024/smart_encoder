"""
This module contains helper functions for formatting data into human-readable strings.
These functions are used throughout the application, particularly in logging, to
present information like time durations and file sizes in a clear and consistent way.
"""

from datetime import timedelta
from pathlib import Path
from typing import List, Any, Dict


def format_timedelta(td_object: timedelta) -> str:
    """
    Formats a timedelta object into a "HH:MM:SS" string.

    This is useful for displaying elapsed times in a standard, easy-to-read format.

    Args:
        td_object: The timedelta object to format.

    Returns:
        A string representing the timedelta in HH:MM:SS format.
        For example, a timedelta of 7261 seconds becomes "02:01:01".
        Returns "00:00:00" if the input is not a valid timedelta object.
    """
    if not isinstance(td_object, timedelta):
        return "00:00:00"

    total_seconds = int(td_object.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def formatted_size(size_bytes: int) -> str:
    """
    Converts a size in bytes to a human-readable string (e.g., B, KB, MB, GB, TB).

    This function makes large file sizes much easier to understand at a glance in
    log messages and reports.

    Args:
        size_bytes: The size of the file in bytes.

    Returns:
        A formatted string with the appropriate unit.
        For example, 1536 becomes "1.50 KB", and 2097152 becomes "2.00 MB".
    """
    if size_bytes < 0:
        size_bytes = 0

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    factor = 1024.0

    if size_bytes == 0:
        return "0 B"

    # Iterate through units until the size is less than the next factor of 1024.
    for unit in units:
        if size_bytes < factor:
            # For bytes, show as an integer. For others, show with two decimal places.
            if unit == "B":
                return f"{size_bytes} {unit}"
            else:
                # Clean up ".00" for whole numbers (e.g., "2.00 MB" -> "2 MB").
                return f"{size_bytes:.2f} {unit}".replace(".00", "")
        size_bytes /= factor

    # If the size is larger than Petabytes, it will be displayed in PB.
    return f"{size_bytes:.2f} {units[-1]}".replace(".00", "")


def contains_any_extensions(
    file_path_obj: Path, extensions_to_check: List[str]
) -> bool:
    """
    Checks if a file's extension is present in a given list (case-insensitive).

    Args:
        file_path_obj: A `pathlib.Path` object for the file to check.
        extensions_to_check: A list of file extensions, including the leading dot
                             (e.g., [".mp4", ".mkv"]).

    Returns:
        True if the file's extension is in the list, False otherwise.
    """
    if not extensions_to_check:
        return False

    # Get the file's extension (e.g., ".mp4") and convert to lowercase.
    file_extension = file_path_obj.suffix.lower()

    # Normalize the list of extensions to ensure they are all lowercase and have a leading dot.
    # Using a set provides faster lookups.
    normalized_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in extensions_to_check
    }

    return file_extension in normalized_extensions


def find_key_in_dictionary(data_dict: Dict[str, Any], target_key: str) -> Any | None:
    """
    Recursively searches for a `target_key` within a nested dictionary.

    This is useful for extracting a specific piece of data from a complex,
    nested structure like the JSON output from ffprobe.

    Args:
        data_dict: The dictionary to search within.
        target_key: The key whose value you want to find.

    Returns:
        The value of the first `target_key` found, or `None` if the key is not
        found anywhere in the nested structure.
    """
    if not isinstance(data_dict, dict):
        return None

    # Check if the key exists at the current level.
    if target_key in data_dict:
        return data_dict[target_key]

    # If not, recursively search in any values that are also dictionaries.
    for value in data_dict.values():
        if isinstance(value, dict):
            found_value = find_key_in_dictionary(value, target_key)
            if found_value is not None:
                return found_value
        # Optional: could also be extended to search in lists of dictionaries.
        # elif isinstance(value, list):
        #     for item in value:
        #         if isinstance(item, dict):
        #             ...

    return None # Key was not found.