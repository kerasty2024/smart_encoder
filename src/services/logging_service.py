"""
This module provides classes for managing application logging.

It separates logging concerns into specific classes for handling errors (ErrorLog)
and successes (SuccessLog). This structured approach allows for robust tracking of
the encoding process. Success logs are written in a machine-readable YAML format,
which facilitates automated processing and reporting, while error logs are in a
human-readable text format for easy debugging.

A key feature is the ability to consolidate multiple temporary success logs, often
created by concurrent processes, into a single, comprehensive final report.
"""

import os
import random
import re
import string
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Set, Union

import yaml
from loguru import logger

# Import configuration constants for filenames and settings.
from ..config.common import (
    COMPLETED_LOG_FILE_NAME,
    SUCCESS_LOG_RANDOM_LENGTH,
    DEFAULT_SUCCESS_LOG_YAML,
)


class Log:
    """
    A base class for all logging operations.

    This class provides common attributes and utility methods that are shared by
    all specific log handlers (like ErrorLog and SuccessLog). Its main purpose is
    to handle the basic setup of log file paths and directories.
    """

    # A decorative separator line used in text-based logs for better readability.
    linesep_marker: str = "=" * 50

    def __init__(self, log_base_path: Path):
        """
        Initializes the Log instance.

        This constructor takes a base path, determines the correct log directory from it,
        and ensures that this directory exists, creating it if necessary.

        Args:
            log_base_path: The base path for logging. If it's a directory,
                           log files will be created inside it. If it's a file path,
                           its parent will be used as the log directory.
        """
        self.log_file_path: Path  # To be defined by the subclass.
        # Determine if the provided path is a directory or a file path.
        if log_base_path.is_dir():
            self.log_dir: Path = log_base_path.resolve()
        else:
            self.log_dir: Path = log_base_path.parent.resolve()
        # Ensure the log directory exists on the filesystem.
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def write(self, log_content: Union[dict, list, str]):
        """
        An abstract method for writing log content.

        Subclasses must implement this method to define their specific writing
        behavior (e.g., writing plain text for errors, or YAML for successes).
        """
        raise NotImplementedError("Subclasses must implement the write() method.")

    @staticmethod
    def generate_random_string(length: int = SUCCESS_LOG_RANDOM_LENGTH) -> str:
        """
        Generates a random string of uppercase letters and digits.

        This is used to create unique filenames for temporary success logs, which
        prevents multiple concurrent processes from trying to write to the same
        file at the same time.

        Args:
            length: The desired length of the random string.

        Returns:
            A random alphanumeric string.
        """
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


class ErrorLog(Log):
    """
    Handles the writing of error logs to a plain text file.

    This class is specialized for logging human-readable error messages. Each new
    error is appended to the log file, making it a chronological record of issues
    that occurred during the application's runtime, which is very useful for debugging.
    """

    DEFAULT_ERROR_FILENAME = "error.txt"

    def __init__(self, error_log_dir: Path, filename: str = DEFAULT_ERROR_FILENAME):
        """
        Initializes the ErrorLog instance.

        Args:
            error_log_dir: The directory where the error log file will be stored.
            filename: The name of the error log file (defaults to "error.txt").
        """
        super().__init__(error_log_dir)
        self.log_file_path = self.log_dir / filename

    def write(self, *error_messages: str):
        """
        Writes one or more error messages to the log file.

        Each call to this method appends the given messages to the file, with each
        message on a new line, followed by a separator line. This formatting helps
        to distinguish between different error events in the log.

        Args:
            *error_messages: A variable number of string arguments, where each string
                             is a piece of the error message to be logged.
        """
        if not error_messages:
            return

        # Join all parts of the message with newlines and add the separator.
        content_to_write = "\n".join(error_messages) + "\n" + self.linesep_marker + "\n"

        try:
            # Open the file in 'append' mode ('a') to add the new error at the end.
            with self.log_file_path.open("a", encoding="utf-8") as f:
                f.write(content_to_write)
        except Exception as e:
            # If writing to the file fails for any reason (e.g., disk full, permissions),
            # use the main application logger (loguru) as a fallback to ensure the
            # error message is not lost.
            logger.error(f"Failed to write to error log {self.log_file_path}: {e}")
            logger.error("Original error messages attempted to log:")
            for msg in error_messages:
                logger.error(f"  - {msg}")


class SuccessLog(Log):
    """
    Handles structured logging for successful operations in YAML format.

    This class is central to tracking successful encodes. Each process writes its
    successes to a temporary, uniquely named log file. This class also provides
    a powerful method to find all these temporary logs, combine them into a single
    master log file, sort them, and clean up the temporary files.
    """

    def __init__(self, success_log_dir: Path, use_dated_filename: bool = True):
        """
        Initializes the SuccessLog instance.

        Args:
            success_log_dir: The directory where the success log file will be stored.
            use_dated_filename: If True, the log filename will include a date and a
                                random string. This is the standard behavior for
                                individual process logs to avoid conflicts. If False,
                                a default, fixed filename is used.
        """
        super().__init__(success_log_dir)

        # Create a unique filename for the log if required.
        if use_dated_filename:
            date_str = datetime.now().strftime("%Y%m%d")
            random_str = self.generate_random_string()
            log_filename = f"log_{date_str}_{random_str}.yaml"
        else:
            log_filename = DEFAULT_SUCCESS_LOG_YAML

        self.log_file_path = self.log_dir / log_filename
        # This will hold the log entries in memory before writing to the file.
        self.log_entries: List[Dict] = []

    def write(self, new_log_entry: dict):
        """
        Writes a new structured log entry to its designated YAML file.

        To ensure the YAML file is always a valid list, this method first reads
        the existing content of the file (if any), appends the new entry to the
        list in memory, and then writes the entire updated list back to the file.

        Args:
            new_log_entry: A dictionary containing the structured data for the successful event.
        """
        if not isinstance(new_log_entry, dict):
            logger.error("SuccessLog.write expects a dictionary as a log entry.")
            return

        # 1. Read existing entries from the file if it exists and is not empty.
        if self.log_file_path.is_file():
            try:
                with self.log_file_path.open("r", encoding="utf-8") as f:
                    loaded_entries = yaml.safe_load(f)
                    # Ensure the loaded content is a list. If not, start fresh.
                    if isinstance(loaded_entries, list):
                        self.log_entries = loaded_entries
                    elif loaded_entries is None:
                        self.log_entries = []  # File was empty.
                    else:
                        logger.warning(
                            f"Success log {self.log_file_path} contained unexpected data. Starting a new log."
                        )
                        self.log_entries = []
            except (yaml.YAMLError, Exception) as e:
                logger.error(
                    f"Error reading/parsing success log {self.log_file_path}: {e}. Starting a new log."
                )
                self.log_entries = []

        # 2. Add the new entry and assign it a new, unique index.
        current_max_index = max(
            (
                entry.get("index", 0)
                for entry in self.log_entries
                if isinstance(entry, dict)
            ),
            default=0,
        )
        new_log_entry["index"] = current_max_index + 1
        self.log_entries.append(new_log_entry)

        # 3. Write the entire list of entries back to the YAML file.
        try:
            with self.log_file_path.open("w", encoding="utf-8") as f:
                yaml.dump(
                    self.log_entries,
                    f,
                    default_flow_style=False,
                    sort_keys=False,
                    allow_unicode=True,
                    indent=4,
                    width=220,
                )
        except Exception as e:
            logger.error(f"Failed to write to success log {self.log_file_path}: {e}")

    @classmethod
    def generate_combined_log_yaml(cls, root_scan_dir: Path):
        """
        Combines all individual success logs into a single, consolidated YAML file.

        This method is a critical part of the post-processing workflow. It performs
        a multi-phase cleanup and consolidation:
        1. It finds all temporary logs (`log_YYYYMMDD_random.yaml`) in all subdirectories
           and merges them into daily summary logs (`log_YYYYMMDD.yaml`).
        2. It then collects all these daily logs, merges them with any existing master
           log (`combined_log.yaml`), sorts all entries chronologically, re-assigns
           a final index to each entry, and writes the final master log.
        3. All temporary and daily logs are deleted in the process.

        Args:
            root_scan_dir: The root directory to scan recursively for log files.
        """
        if not root_scan_dir or not root_scan_dir.is_dir():
            logger.error(
                f"Cannot generate combined log: Invalid root scan directory provided: {root_scan_dir}"
            )
            return

        all_discovered_entries: List[Dict] = []

        # --- Phase 1: Combine process-specific logs into daily logs per directory ---
        # Find all directories that contain our temporary random logs.
        log_file_parent_dirs: Set[Path] = {
            p.parent for p in root_scan_dir.rglob("log_????????_*.yaml")
        }

        for log_dir_path in log_file_parent_dirs:
            # Group the random logs by their date string (e.g., "20230101").
            logs_by_date: Dict[str, List[Path]] = {}
            for random_log_file in log_dir_path.glob("log_????????_*.yaml"):
                match = re.search(r"log_(\d{8})_", random_log_file.name)
                if match:
                    logs_by_date.setdefault(match.group(1), []).append(random_log_file)

            # For each date, merge all random logs into one daily log.
            for date_str, files_for_date in logs_by_date.items():
                date_specific_entries: List[Dict] = []
                for log_file_path in files_for_date:
                    try:
                        with log_file_path.open("r", encoding="utf-8") as f:
                            content = yaml.safe_load(f)
                            if isinstance(content, list):
                                date_specific_entries.extend(content)
                        log_file_path.unlink()  # Delete the temporary file.
                    except Exception as e:
                        logger.error(
                            f"Error processing temporary log {log_file_path}: {e}"
                        )

                if date_specific_entries:
                    combined_date_log_path = log_dir_path / f"log_{date_str}.yaml"
                    try:
                        # Append to an existing daily log if one is already there.
                        existing_entries = []
                        if combined_date_log_path.exists():
                            with combined_date_log_path.open(
                                "r", encoding="utf-8"
                            ) as f_exist:
                                loaded = yaml.safe_load(f_exist)
                                if isinstance(loaded, list):
                                    existing_entries = loaded
                        with combined_date_log_path.open(
                            "w", encoding="utf-8"
                        ) as f_out:
                            yaml.dump(
                                existing_entries + date_specific_entries,
                                f_out,
                                default_flow_style=False,
                                sort_keys=False,
                                allow_unicode=True,
                                indent=4,
                                width=220,
                            )
                    except Exception as e:
                        logger.error(
                            f"Error writing daily log {combined_date_log_path}: {e}"
                        )

        # --- Phase 2: Collect all daily logs and merge into the final master log ---
        log_files_to_combine = list(root_scan_dir.rglob("log_????????.yaml"))

        for log_file_path in log_files_to_combine:
            try:
                with log_file_path.open("r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, list):
                        all_discovered_entries.extend(content)
                log_file_path.unlink()  # Delete the daily log.
            except Exception as e:
                logger.error(f"Error processing daily log {log_file_path}: {e}")

        # Merge with any pre-existing master log file.
        final_combined_log_path = root_scan_dir / COMPLETED_LOG_FILE_NAME
        if final_combined_log_path.is_file():
            try:
                with final_combined_log_path.open("r", encoding="utf-8") as f:
                    existing_combined_content = yaml.safe_load(f)
                    if isinstance(existing_combined_content, list):
                        all_discovered_entries.extend(existing_combined_content)
            except Exception as e:
                logger.error(
                    f"Error reading existing master log {final_combined_log_path}: {e}"
                )

        if not all_discovered_entries:
            logger.debug("No new log entries found to combine.")
            return

        # --- Final Step: Sort all entries chronologically and re-index them ---
        try:
            # Separate entries that can be sorted from those that can't (missing the key).
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

            sortable_entries.sort(key=lambda x: x.get("ended_datetime", ""))

            combined_final_entries = sortable_entries + unsortable_entries
            # Re-assign the 'index' to be sequential in the final sorted list.
            for i, entry_dict in enumerate(combined_final_entries, start=1):
                if isinstance(entry_dict, dict):
                    entry_dict["index"] = i
        except Exception as sort_ex:
            logger.error(
                f"Error sorting/re-indexing log entries: {sort_ex}. Writing unsorted."
            )
            combined_final_entries = all_discovered_entries

        # Write the final, consolidated master log file.
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