import re
import shutil
from pathlib import Path

import yaml
from loguru import logger

from scripts.settings.audio import AUDIO_EXTENSIONS
from scripts.settings.common import DEFAULT_SUCCESS_LOG_YAML, MINIMUM_FILE_SIZE
from scripts.settings.video import EXCEPT_FOLDERS_KEYWORDS, VIDEO_EXTENSIONS


class ProcessFiles:
    """
    Base class for file processing. Must be inherited and overridden.
    """

    dirs: set[Path] = set()
    files: tuple[Path, ...] = tuple()

    def __init__(self, path: Path = None, args=None):
        self.source_dir = self._get_source_directory(path)
        if self.source_dir is None:
            logger.error(f"No file/directory found: {path}")
            return

        self.args = args
        self.set_dirs()
        self.standardize_dir_names()
        self.set_files()
        if not getattr(self.args, "not_rename", False):
            self.standardize_file_names()

    @staticmethod
    def _get_source_directory(path: Path) -> Path:
        """
        Returns the absolute path of the directory containing the file or the directory itself.

        :param path: Path object of the file or directory.
        :return: Absolute Path of the directory or None if the path is invalid.
        """
        if path is None:
            return None
        if path.is_file():
            return path.parent
        elif path.is_dir():
            return path
        return None

    def set_files(self):
        """
        Method to be overridden in subclasses to set the files list.
        """
        pass

    def set_dirs(self):
        """
        Sets directories for processing, excluding those with specified keywords if not in manual mode.
        """

        def contains_excluded_keywords(path: Path):
            return any(
                keyword in path.as_posix().lower()
                for keyword in EXCEPT_FOLDERS_KEYWORDS
            )

        search_pattern = self.source_dir.glob("**")
        dirs = [
            d.resolve()
            for d in search_pattern
            if d.is_dir()
               and (self.args.manual_mode or not contains_excluded_keywords(d))
        ]
        self.dirs = set(dirs)

    def remove_empty_dirs(self):
        """
        Removes empty directories and handles potential exceptions.
        """
        empty_dirs = [
            d for d in self.source_dir.glob("**") if d.is_dir() and not any(d.iterdir())
        ]

        for empty_dir in empty_dirs:
            try:
                empty_dir.rmdir()
            except OSError as e:
                if e.errno == 2:  # File not found
                    continue
                if e.errno == 5:  # Access denied
                    self._handle_access_denied(empty_dir)
                logger.error(f"Cannot delete {empty_dir}: {e}")
            self.remove_empty_dirs()

    @staticmethod
    def _handle_access_denied(directory: Path):
        """
        Handles access denied errors by changing the permissions of subdirectories.

        :param directory: The directory for which access was denied.
        """
        for sub_dir in directory.glob("**"):
            if sub_dir.is_dir():
                sub_dir.chmod(0o777)

    def delete_temp_folders(self):
        """
        Deletes temporary folders matching certain patterns.
        """
        temp_patterns = [".ab-av1-*", ".temp*"]
        temp_dirs = [
            d
            for pattern in temp_patterns
            for d in self.source_dir.glob(f"**/{pattern}")
            if d.is_dir()
        ]

        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.set_dirs()

    def move_raw_folder_if_no_process_files(self, dst: Path):
        """
        Moves raw folders that do not contain process files to the destination.
        """
        pass

    def standardize_file_names(self):
        """
        Renames files to remove Korean characters and other unwanted characters.
        """

        def remove_korean_chars(word: str) -> str:
            return re.sub(
                r"[\uac00-\ud7af\u3200-\u321f\u3260-\u327f\u1100-\u11ff\u3130-\u318f\uffa0-\uffdf"
                r"\ua960-\ua97f\ud7b0-\ud7ff]+",
                "",
                word,
            )

        for file in self.files:
            new_file_name = remove_korean_chars(file.name)
            new_file_path = file.with_name(new_file_name)
            if new_file_path != file:
                try:
                    logger.info(f"Renaming file: {file} to: {new_file_path}")
                    file.rename(new_file_path)
                    self.set_files()
                except Exception as e:
                    logger.error(f"Error renaming file {file} to {new_file_path}: {e}")

    def standardize_dir_names(self):
        """
        Renames directories to replace unwanted characters.
        """

        def replace_unwanted_chars(dir_name):
            return dir_name.replace(".", "").replace("[", "(").replace("]", ")")

        self.set_dirs()
        for directory in self.dirs:
            new_dir_name = replace_unwanted_chars(directory.name)
            new_dir_path = directory.with_name(new_dir_name)
            if new_dir_path != directory:
                try:
                    logger.info(f"Renaming directory: {directory} to: {new_dir_path}")
                    directory.rename(new_dir_path)
                except FileNotFoundError as e:
                    logger.error(f"Directory not found: {directory}: {e}")
                except FileExistsError as e:
                    logger.warning(f"Directory already exists: {new_dir_name}: {e}")
                    shutil.copytree(directory, new_dir_path, dirs_exist_ok=True)
                    shutil.rmtree(directory)
                except Exception as e:
                    logger.error(
                        f"Error renaming directory {directory} to {new_dir_name}: {e}"
                    )

                self.standardize_dir_names()  # Recursive call to handle nested directories

    @classmethod
    def get_relative_root_dir(
            cls, root_dir: Path = Path.cwd(), target_path: Path = None
    ) -> str:
        """
        Returns the relative path of the target_path with respect to root_dir.
        """
        root_dir = root_dir.resolve()
        target_path = target_path.resolve()
        if root_dir in target_path.parents:
            return str(target_path.relative_to(root_dir))
        return cls.get_relative_root_dir(root_dir, target_path.parent)

    def remove_small_files(self):
        for file in self.files:
            if file.stat().st_size < MINIMUM_FILE_SIZE:
                file.unlink(missing_ok=True)
                logger.info(f'deleted file: {file.resolve()} ({file.stat().st_size} B)')


class ProcessVideoFiles(ProcessFiles):
    def set_files(self):
        """
        Overrides to set files with video extensions.
        """
        self.files = tuple(
            sorted(
                f
                for ext in VIDEO_EXTENSIONS
                for d in self.dirs
                for f in d.glob(f"*{ext}")
            )
        )

    def move_raw_folder_if_no_process_files(self, dst: Path):
        """
        Moves raw folders that do not contain process files to the destination.
        """
        if not self.source_dir:
            return

        for directory in self.dirs - {self.source_dir}:
            process_files = ProcessVideoFiles(directory, self.args)
            if not directory.is_dir() or process_files.files:
                continue

            target = dst / directory.relative_to(self.source_dir)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(directory), str(target))
            except OSError:
                shutil.copytree(directory, target, dirs_exist_ok=True)
                shutil.rmtree(directory)
            except Exception as e:
                logger.error(f"Error moving directory {directory} to {target}: {e}")


class ProcessPhoneFiles(ProcessVideoFiles):
    def set_files(self):
        """
        Overrides to set files, excluding those listed in the success log.
        """
        processed_files = set()
        if Path(DEFAULT_SUCCESS_LOG_YAML).is_file():
            with open(DEFAULT_SUCCESS_LOG_YAML, encoding="utf-8") as f:
                success_log_list = yaml.safe_load(f)
                processed_files = {
                    Path(entry.get("input file")).stem for entry in success_log_list
                }

        self.files = tuple(
            sorted(
                f
                for ext in VIDEO_EXTENSIONS
                for d in self.dirs
                for f in d.glob(f"*{ext}")
                if f.stem not in processed_files
            )
        )


class ProcessAudioFiles(ProcessFiles):
    def set_files(self):
        """
        Overrides to set files with audio extensions.
        """
        self.files = tuple(
            sorted(
                f
                for ext in AUDIO_EXTENSIONS
                for d in self.dirs
                for f in d.glob(f"*{ext}")
            )
        )
