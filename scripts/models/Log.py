import os
import random
import re
import string
from datetime import datetime
from pathlib import Path

import yaml
from loguru import logger

from scripts.settings.common import (
    COMPLETED_LOG_FILE_NAME,
    SUCCESS_LOG_RANDOM_LENGTH,
    DEFAULT_SUCCESS_LOG_YAML,
)


class Log:
    """
    Base class for handling logging operations. Provides utility functions and attributes
    common to all types of logs, such as generating random strings and setting up paths.
    """

    linesep: str = "=" * 50  # Separator line for log entries

    def __init__(self, path: Path):
        """
        Initialize the Log instance with the specified path.

        Args:
            path (Path): The path to the log file or directory.

        Attributes:
            file (Path): Path to the log file.
            file_name (str): Name of the log file.
            dir (Path): Directory path of the log file.
        """
        self.file: Path = path if path.is_file() else None
        self.file_name = self.file.name if self.file else None
        self.dir = Path(path).parent if path.is_file() else Path(path)

    def write(self, log_dic: dict = None):
        """
        Placeholder method for writing logs. This should be implemented by subclasses
        for specific logging behaviors.

        Args:
            log_dic (dict): Dictionary containing log data.
        """
        pass

    @classmethod
    def generate_random_string(cls, length: int = SUCCESS_LOG_RANDOM_LENGTH) -> str:
        """
        Generate a random string of uppercase letters and digits.

        Args:
            length (int): Length of the random string to generate.

        Returns:
            str: Generated random string.
        """
        return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


class ErrorLog(Log):
    """
    Class for handling error logging. Inherits from Log and provides implementation
    for writing error messages to a file.
    """

    def __init__(self, path: Path):
        """
        Initialize the ErrorLog instance, setting the default error log file.

        Args:
            path (Path): Path to the error log directory or file.
        """
        super().__init__(path)
        if not self.file:
            self.file = self.dir / "error.txt"

    def write(self, *args):
        """
        Write error messages to the error log file.

        Args:
            *args: Variable length argument list for error messages to be logged.
        """
        contents = "\n".join(args) + "\n" + self.linesep
        with self.file.open("a", encoding="utf-8") as f:
            f.write(contents)


class SuccessLog(Log):
    """
    Class for handling success logging. Inherits from Log and provides implementation
    for writing and combining success logs.
    """

    def __init__(self, path: Path, log_date: bool = True):
        """
        Initialize the SuccessLog instance, setting the file name with or without a date prefix.

        Args:
            path (Path): Path to the success log directory or file.
            log_date (bool): Whether to include the date in the file name.
        """
        super().__init__(path)
        self.file_name = (
            f"log_{datetime.now().strftime('%Y%m%d')}_{self.generate_random_string()}.yaml"
            if log_date
            else DEFAULT_SUCCESS_LOG_YAML
        )
        self.file = self.dir / self.file_name
        self.contents = []

    def write(self, log_dic: dict = None):
        """
        Write the log dictionary to the success log file. Updates existing logs or creates new logs if necessary.

        Args:
            log_dic (dict): Dictionary containing success log data.
        """
        if self.file.is_file():
            with self.file.open("r", encoding="utf-8") as f:
                try:
                    self.contents = yaml.full_load(f) or []
                except yaml.constructor.ConstructorError as CE:
                    logger.error(CE)
                    os.remove(f)

        index = len(self.contents) + 1
        log_dic.update({"index": index})
        self.contents.append(log_dic)
        with self.file.open("w", encoding="utf-8") as f:
            yaml.dump(
                self.contents,
                f,
                default_flow_style=False,
                sort_keys=False,
                encoding="utf-8",
                allow_unicode=True,
                indent=4,
                width=220,
            )

    @classmethod
    def generate_combined_log_yaml(cls, pardir: Path = None):
        """
        Combine all success logs in a directory into a single YAML file, sorted by 'ended time'.

        Args:
            pardir (Path): Parent directory containing log files to be combined.
        """

        def combine_yaml_in_single_folder(dirs: set[Path]):
            """
            Combine YAML files in the given directories, grouping by date.

            Args:
                dirs (set[Path]): Set of directories containing YAML files to be combined.
            """
            for _dir in dirs:
                # Extract unique dates from log file names
                dates = {
                    re.search(r"2[0-9]{7}", _log_file.name).group(0)
                    for _log_file in _dir.glob("log_*.yaml")
                }
                for date in dates:
                    _contents = []
                    same_date_log_files = {
                        _f for _f in _dir.glob("log_*.yaml") if date in _f.name
                    }
                    for same_date_log_file in same_date_log_files:
                        with same_date_log_file.open("r", encoding="utf-8") as _f:
                            _contents.extend(yaml.full_load(_f) or [])
                        same_date_log_file.unlink()  # Remove individual log file after reading
                    with (_dir / f"log_{date}.yaml").open("w", encoding="utf-8") as _f:
                        yaml.dump(
                            _contents,
                            _f,
                            default_flow_style=False,
                            sort_keys=False,
                            encoding="utf-8",
                            allow_unicode=True,
                            indent=4,
                            width=220,
                        )

        contents = []
        combined_log_file = Path(pardir) / COMPLETED_LOG_FILE_NAME
        if combined_log_file.is_file():
            with combined_log_file.open("r", encoding="utf-8") as f:
                try:
                    contents = yaml.full_load(f) or []
                except yaml.constructor.ConstructorError as CE:
                    logger.error(CE)
                    os.remove(f)
        log_dirs = set()

        # Collect all logs and directories
        log_files = set(Path(pardir).rglob("log_*.yaml"))
        for log_file in log_files:
            log_dirs.add(log_file.parent)
            with log_file.open("r", encoding="utf-8") as f:
                contents.extend(yaml.full_load(f) or [])
            log_file.unlink()  # Remove individual log file after reading

        combine_yaml_in_single_folder(log_dirs)
        logger.debug(contents)

        # Sort and save combined logs
        contents.sort(key=lambda x: x.get("ended time", ""))
        for index, dic in enumerate(contents, start=1):
            dic.update({"index": index})

        with combined_log_file.open("w", encoding="utf-8") as f:
            yaml.dump(
                contents,
                f,
                default_flow_style=False,
                sort_keys=False,
                encoding="utf-8",
                allow_unicode=True,
                indent=4,
                width=220,
            )
