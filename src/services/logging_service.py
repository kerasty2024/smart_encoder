import os
import random
import re
import string
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set  # For type hinting

import yaml
from loguru import logger

# Config
from ..config.common import (
    COMPLETED_LOG_FILE_NAME,
    SUCCESS_LOG_RANDOM_LENGTH,
    DEFAULT_SUCCESS_LOG_YAML,
)


class Log:
    """
    Base class for handling logging operations. Provides utility functions and attributes
    common to all types of logs, such as generating random strings and setting up paths.
    """

    linesep_marker: str = (
        "=" * 50
    )  # Separator line for log entries. Renamed from linesep to avoid conflict.

    def __init__(
        self, log_base_path: Path
    ):  # Path can be dir or specific file for ErrorLog
        """
        Initialize the Log instance.

        Args:
            log_base_path (Path): The base path. If it's a directory, log files will be created inside.
                                  If it's a file path (for ErrorLog), it's used directly.
        """
        self.log_file_path: Path  # To be set by subclass
        if log_base_path.is_dir():
            self.log_dir: Path = log_base_path.resolve()
        else:  # Assumed to be a file path, or a base for a file path
            self.log_dir: Path = log_base_path.parent.resolve()

        self.log_dir.mkdir(parents=True, exist_ok=True)  # Ensure log directory exists

    def write(self, log_content: dict | list | str):  # More flexible type hint
        """
        Placeholder method for writing logs. This should be implemented by subclasses.
        """
        raise NotImplementedError("Subclasses must implement the write() method.")

    @staticmethod  # Changed to staticmethod as it doesn't use 'cls' or 'self'
    def generate_random_string(length: int = SUCCESS_LOG_RANDOM_LENGTH) -> str:
        """
        Generate a random string of uppercase letters and digits.
        """
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


class ErrorLog(Log):
    """
    Class for handling error logging. Inherits from Log and provides implementation
    for writing error messages to a file.
    """

    DEFAULT_ERROR_FILENAME = "error.txt"

    def __init__(self, error_log_dir: Path, filename: str = DEFAULT_ERROR_FILENAME):
        """
        Initialize the ErrorLog instance.

        Args:
            error_log_dir (Path): Path to the directory where the error log file will be stored.
            filename (str): Name of the error log file.
        """
        super().__init__(error_log_dir)  # log_dir is set by parent
        self.log_file_path = self.log_dir / filename

    def write(self, *error_messages: str):  # Takes variable string arguments
        """
        Write error messages to the error log file, each on a new line,
        followed by a separator.
        """
        if not error_messages:
            return

        # Join messages with newlines, add a final newline and separator
        content_to_write = "\n".join(error_messages) + "\n" + self.linesep_marker + "\n"

        try:
            with self.log_file_path.open("a", encoding="utf-8") as f:
                f.write(content_to_write)
        except Exception as e:
            # Fallback logging if file write fails
            logger.error(f"Failed to write to error log {self.log_file_path}: {e}")
            logger.error("Original error messages attempted to log:")
            for msg in error_messages:
                logger.error(f"  - {msg}")


class SuccessLog(Log):
    """
    Class for handling success logging in YAML format. Inherits from Log.
    Each instance typically manages one YAML log file which can contain multiple entries.
    """

    def __init__(self, success_log_dir: Path, use_dated_filename: bool = True):
        """
        Initialize the SuccessLog instance.

        Args:
            success_log_dir (Path): Directory to store the success log file.
            use_dated_filename (bool): Whether to include the current date in the log file name.
                                       If False, uses DEFAULT_SUCCESS_LOG_YAML from config.
        """
        super().__init__(success_log_dir)  # log_dir is set by parent

        if use_dated_filename:
            date_str = datetime.now().strftime("%Y%m%d")
            random_str = self.generate_random_string()
            log_filename = f"log_{date_str}_{random_str}.yaml"
        else:
            log_filename = DEFAULT_SUCCESS_LOG_YAML  # From config

        self.log_file_path = self.log_dir / log_filename
        self.log_entries: List[Dict] = []  # In-memory list of log entries for this file

    def write(self, new_log_entry: dict):
        """
        Adds a new log entry to the YAML file. Reads existing entries, appends the new one,
        and writes back the entire list.

        Args:
            new_log_entry (dict): Dictionary containing the success log data for one event.
        """
        if not isinstance(new_log_entry, dict):
            logger.error("SuccessLog.write expects a dictionary log_entry.")
            return

        # Load existing entries if file exists
        if self.log_file_path.is_file():
            try:
                with self.log_file_path.open("r", encoding="utf-8") as f:
                    # yaml.safe_load can return None for empty file, ensure it's a list
                    loaded_entries = yaml.safe_load(f)
                    if isinstance(loaded_entries, list):
                        self.log_entries = loaded_entries
                    elif loaded_entries is None:  # Empty file
                        self.log_entries = []
                    else:  # Unexpected content
                        logger.warning(
                            f"Success log {self.log_file_path} contained unexpected data type. Starting fresh."
                        )
                        self.log_entries = []
            except yaml.YAMLError as ye:  # Error during YAML parsing
                logger.error(
                    f"Error parsing existing success log {self.log_file_path}: {ye}. Log file might be corrupted. Starting fresh for this instance."
                )
                # Optionally, backup corrupted file before overwriting
                # e.g., self.log_file_path.rename(self.log_file_path.with_suffix('.yaml.corrupted'))
                self.log_entries = []
            except Exception as e:
                logger.error(
                    f"Unexpected error reading success log {self.log_file_path}: {e}. Starting fresh for this instance."
                )
                self.log_entries = []

        # Add new entry, assign index
        current_max_index = 0
        if self.log_entries:  # Find max current index to ensure new one is unique if "index" is already there
            for entry in self.log_entries:
                if (
                    isinstance(entry, dict)
                    and "index" in entry
                    and isinstance(entry["index"], int)
                ):
                    current_max_index = max(current_max_index, entry["index"])

        new_log_entry["index"] = current_max_index + 1  # Assign new index
        self.log_entries.append(new_log_entry)

        # Write all entries back to the file
        try:
            with self.log_file_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    self.log_entries,
                    f,
                    default_flow_style=False,
                    sort_keys=False,  # Keep insertion order of keys in dicts
                    allow_unicode=True,
                    indent=4,
                    width=220,  # Line width hint
                )
        except Exception as e:
            logger.error(f"Failed to write to success log {self.log_file_path}: {e}")

    @classmethod
    def generate_combined_log_yaml(cls, root_scan_dir: Path):
        """
        Combines all individual `log_YYYYMMDD_*.yaml` files found recursively
        within `root_scan_dir` into a single `combined_log.yaml` in `root_scan_dir`.
        Also processes per-directory `log_YYYYMMDD.yaml` files.
        Individual dated logs are deleted after merging.
        """
        if not root_scan_dir or not root_scan_dir.is_dir():
            logger.error(
                f"generate_combined_log_yaml: Invalid root_scan_dir: {root_scan_dir}"
            )
            return

        all_discovered_entries: List[Dict] = []

        # Part 1: Combine multi-random-string logs within each directory by date
        # e.g., log_20230101_abc.yaml + log_20230101_def.yaml -> log_20230101.yaml (in same dir)

        # Find all unique directories containing log_YYYYMMDD_*.yaml files
        log_file_parent_dirs: Set[Path] = set()
        for random_log_file in root_scan_dir.rglob(
            "log_????????_*.yaml"
        ):  # Matches log_YYYYMMDD_random.yaml
            log_file_parent_dirs.add(random_log_file.parent)

        for log_dir_path in log_file_parent_dirs:
            # Group random logs by date within this directory
            logs_by_date: Dict[str, List[Path]] = {}  # "YYYYMMDD" -> [Path1, Path2]
            for random_log_file in log_dir_path.glob("log_????????_*.yaml"):
                match = re.search(r"log_(\d{8})_", random_log_file.name)
                if match:
                    date_str = match.group(1)
                    logs_by_date.setdefault(date_str, []).append(random_log_file)

            for date_str, files_for_date in logs_by_date.items():
                date_specific_entries: List[Dict] = []
                for log_file_path in files_for_date:
                    try:
                        with log_file_path.open("r", encoding="utf-8") as f:
                            content = yaml.safe_load(f)
                            if isinstance(content, list):
                                date_specific_entries.extend(content)
                        log_file_path.unlink()  # Delete individual random log after processing
                        logger.debug(
                            f"Processed and deleted individual random log: {log_file_path}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error processing individual random log {log_file_path}: {e}"
                        )

                # Write combined entries to log_YYYYMMDD.yaml in the same directory
                if date_specific_entries:
                    combined_date_log_path = log_dir_path / f"log_{date_str}.yaml"
                    try:
                        # If combined_date_log_path already exists, append to its content
                        existing_entries_for_date: List[Dict] = []
                        if combined_date_log_path.exists():
                            with combined_date_log_path.open(
                                "r", encoding="utf-8"
                            ) as f_exist:
                                loaded = yaml.safe_load(f_exist)
                                if isinstance(loaded, list):
                                    existing_entries_for_date = loaded

                        all_entries_for_this_date = (
                            existing_entries_for_date + date_specific_entries
                        )

                        with combined_date_log_path.open(
                            "w", encoding="utf-8"
                        ) as f_out:
                            yaml.dump(
                                all_entries_for_this_date,
                                f_out,
                                default_flow_style=False,
                                sort_keys=False,
                                allow_unicode=True,
                                indent=4,
                                width=220,
                            )
                        logger.debug(
                            f"Combined random logs into dated log: {combined_date_log_path}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Error writing combined dated log {combined_date_log_path}: {e}"
                        )

        # Part 2: Collect all entries from log_YYYYMMDD.yaml files and DEFAULT_SUCCESS_LOG_YAML
        # These are now the source for the final combined_log.yaml at root_scan_dir.
        log_files_to_combine = list(
            root_scan_dir.rglob("log_????????.yaml")
        )  # Matches log_YYYYMMDD.yaml

        # Also check for DEFAULT_SUCCESS_LOG_YAML (e.g., "success_log.yaml") in subdirs
        # This might be from older runs or non-dated logs.
        # Original code implies DEFAULT_SUCCESS_LOG_YAML is processed by SuccessLog.write,
        # but generate_combined_log_yaml should also sweep them up if they exist.
        # Let's assume for now that DEFAULT_SUCCESS_LOG_YAML are primarily handled by individual writes,
        # and this combiner focuses on the dated logs. If they need to be combined here, add:
        # log_files_to_combine.extend(list(root_scan_dir.rglob(DEFAULT_SUCCESS_LOG_YAML)))

        for log_file_path in log_files_to_combine:
            try:
                with log_file_path.open("r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, list):
                        all_discovered_entries.extend(content)
                log_file_path.unlink()  # Delete dated log after processing for final combine
                logger.debug(f"Collected and deleted dated log: {log_file_path}")
            except Exception as e:
                logger.error(
                    f"Error processing dated log {log_file_path} for final combination: {e}"
                )

        # Also, check for a pre-existing combined_log.yaml in the root_scan_dir to merge with
        final_combined_log_path = root_scan_dir / COMPLETED_LOG_FILE_NAME  # From config
        if final_combined_log_path.is_file():
            try:
                with final_combined_log_path.open("r", encoding="utf-8") as f:
                    existing_combined_content = yaml.safe_load(f)
                    if isinstance(existing_combined_content, list):
                        all_discovered_entries.extend(existing_combined_content)
                        logger.debug(
                            f"Merged with existing content from {final_combined_log_path}"
                        )
            except Exception as e:
                logger.error(
                    f"Error reading existing final combined log {final_combined_log_path}: {e}"
                )

        if not all_discovered_entries:
            logger.info("No log entries found to combine.")
            # If final_combined_log_path exists but is now empty, we might want to delete it or leave it.
            # For now, if no entries, don't write an empty file unless it didn't exist.
            if not final_combined_log_path.exists():
                return
                # If it exists and all_discovered_entries is empty, it means it was empty to begin with or cleared.

        # Sort entries by 'ended_datetime' (if present) and re-index
        try:
            # Filter out entries that are not dicts or don't have 'ended_datetime' for robust sorting
            sortable_entries = [
                e
                for e in all_discovered_entries
                if isinstance(e, dict) and "ended_datetime" in e
            ]
            unsortable_entries = [
                e
                for e in all_discovered_entries
                if not (isinstance(e, dict) and "ended_datetime" in e)
            ]

            sortable_entries.sort(
                key=lambda x: x.get("ended_datetime", "")
            )  # Sort by time

            combined_final_entries = (
                sortable_entries + unsortable_entries
            )  # Put unsortable at end

            for i, entry_dict in enumerate(combined_final_entries, start=1):
                if isinstance(entry_dict, dict):  # Ensure it's a dict before updating
                    entry_dict["index"] = i  # Re-assign index

        except Exception as sort_ex:  # Catch errors during sorting/re-indexing
            logger.error(
                f"Error during sorting or re-indexing combined log entries: {sort_ex}. Writing unsorted/partially indexed."
            )
            # Fallback: use all_discovered_entries as is, or try to index without sorting.
            for i, entry_dict in enumerate(all_discovered_entries, start=1):
                if isinstance(entry_dict, dict):
                    entry_dict["index"] = i
            combined_final_entries = all_discovered_entries

        # Write the final combined log
        try:
            with final_combined_log_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    combined_final_entries,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    indent=4,
                    width=220,
                )
            logger.info(f"Successfully generated combined log: {final_combined_log_path} with {len(combined_final_entries)} entries.")
        except Exception as e:
            logger.error(f"Failed to write final combined log {final_combined_log_path}: {e}")