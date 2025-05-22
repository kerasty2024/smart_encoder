from datetime import timedelta  # Ensure this is imported if not already
from pathlib import Path
from typing import List, Any, Dict  # Added Any, Dict for find_key_in_dictionary


def format_timedelta(td_object: timedelta) -> str:
    """
    Formats a timedelta object into a string "HH:MM:SS".

    Args:
        td_object: The timedelta object to format.

    Returns:
        A string representing the timedelta in HH:MM:SS format.
    """
    if not isinstance(td_object, timedelta):
        # Handle cases where td_object might be None or other types gracefully
        # Or raise TypeError("Input must be a timedelta object")
        return "00:00:00"

    total_seconds = int(td_object.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def formatted_size(size_bytes: int) -> str:
    """
    Converts a size in bytes to a human-readable string format (B, KB, MB, GB, TB).

    Args:
        size_bytes: Size in bytes.

    Returns:
        Formatted string with appropriate unit.
    """
    if size_bytes < 0:
        size_bytes = 0  # Handle negative sizes if they can occur

    units = ["B", "KB", "MB", "GB", "TB", "PB"]  # Added PB for larger sizes
    factor = 1024.0

    if size_bytes == 0:
        return "0 B"

    for unit in units:
        if size_bytes < factor:
            # Format with 2 decimal places for KB and above if not whole number, 0 for B
            if unit == "B":
                return f"{size_bytes} {unit}"
            else:
                # Example: 1.50 KB, 23.00 MB. Use {:.0f} if no decimals desired for integer results.
                return f"{size_bytes:.2f} {unit}".replace(
                    ".00", ""
                )  # Clean up .00 for whole numbers
        size_bytes /= factor

    # If it's larger than PB, it will return in PB with a large number.
    # Or, handle exabytes (EB) if needed.
    return f"{size_bytes:.2f} {units[-1]}".replace(
        ".00", ""
    )  # Should be last unit used


def contains_any_extensions(
    file_path_obj: Path, extensions_to_check: List[str]
) -> bool:
    """
    Checks if the file path's extension is in the provided list of extensions (case-insensitive).

    Args:
        file_path_obj: Path object of the file.
        extensions_to_check: List of file extensions (e.g., [".txt", ".jpg"]).
                             Should include the leading dot.

    Returns:
        True if the file's extension is in the list, False otherwise.
    """
    if not extensions_to_check:
        return False

    file_extension = file_path_obj.suffix.lower()  # Get ".ext" and lowercase it

    # Normalize extensions_to_check to be lowercase and have leading dot
    normalized_extensions = {
        ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        for ext in extensions_to_check
    }

    return file_extension in normalized_extensions


def find_key_in_dictionary(data_dict: Dict[str, Any], target_key: str) -> Any | None:
    """
    Recursively searches for a `target_key` in a nested dictionary.

    Args:
        data_dict: The dictionary to search within.
        target_key: The key to find.

    Returns:
        The value of the first found `target_key`, or None if the key is not found
        or if `data_dict` is not a dictionary.
    """
    if not isinstance(data_dict, dict):
        return None

    if target_key in data_dict:
        return data_dict[target_key]

    for key, value in data_dict.items():
        # Recursively search in sub-dictionaries
        if isinstance(value, dict):
            found_value = find_key_in_dictionary(value, target_key)
            if found_value is not None:  # Key found in sub-dictionary
                return found_value
        # Optionally, search in lists of dictionaries too
        # elif isinstance(value, list):
        #     for item in value:
        #         if isinstance(item, dict):
        #             found_in_list = find_key_in_dictionary(item, target_key)
        #             if found_in_list is not None:
        #                 return found_in_list

    return None # Key not found at this level or in any sub-dictionaries