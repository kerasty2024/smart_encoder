"""
Provides services for discovering, managing, and cleaning up media files.

This module contains the logic for the initial phase of the pipeline, where
the application scans for files to process. The services here are responsible for:
- Finding all relevant media files (video or audio) in a directory tree.
- Excluding irrelevant directories (like output folders).
- Performing cleanup tasks such as deleting small/invalid files, removing empty
  directories, and standardizing filenames to prevent issues with other tools.
"""

import re
import shutil
from pathlib import Path
from typing import Set, Tuple, Any, Optional

import yaml
from loguru import logger

# Config
from ..config.audio import AUDIO_EXTENSIONS
from ..config.common import DEFAULT_SUCCESS_LOG_YAML, MINIMUM_FILE_SIZE, BASE_ERROR_DIR
from ..config.video import EXCEPT_FOLDERS_KEYWORDS, VIDEO_EXTENSIONS
from ..utils.format_utils import formatted_size


class ProcessFiles:
    """
    A base class for discovering and managing files to be processed.

    This class provides the core functionality for scanning a directory, finding
    subdirectories, and managing a list of files. It is not intended to be used
    directly but should be subclassed (e.g., by `ProcessVideoFiles`) to implement
    the logic for finding specific types of files.

    Responsibilities:
    - Determine the root source directory from a given path.
    - Recursively scan for all subdirectories, respecting exclusion keywords.
    - Provide methods for standardizing filenames, removing small files, and
      cleaning up empty or temporary directories.

    Attributes:
        dirs (Set[Path]): A set of all directories to be scanned for files.
        files (Tuple[Path, ...]): A tuple of all discovered media files to be processed.
        source_dir (Path | None): The root directory for the entire operation.
        args (Any): The parsed command-line arguments.
    """
    dirs: Set[Path] = set()
    files: Tuple[Path, ...] = tuple()
    source_dir: Path | None = None
    args: Any

    def __init__(self, path: Path, args: Optional[Any] = None):
        """
        Initializes the file processor.

        It determines the source directory from the input path and then calls
        methods to discover all relevant directories and files. It may also
        standardize filenames if not disabled by the '--not-rename' flag.

        Args:
            path: The input path, which can be a file or a directory.
            args: The parsed command-line arguments from the CLI.
        """
        self.args = args
        self.source_dir = self._get_source_directory_from_path(path)

        if self.source_dir is None:
            logger.warning(
                f"No valid source file/directory found for path: {path}. Processing will be skipped."
            )
            return

        self.set_dirs_to_scan()
        self.set_files_to_process() # self.files が設定される

        # standardize_discovered_file_names は self.files の内容を更新する可能性がある
        if not getattr(self.args, "not_rename", False) and self.files:
            logger.debug("Standardizing discovered file names (if necessary).")
            self.standardize_discovered_file_names()
        else:
            logger.debug("Skipping file name standardization (--not-rename is set or no files).")


    @staticmethod
    def _get_source_directory_from_path(input_path: Path) -> Path | None:
        """
        Resolves an input path to a valid source directory.

        If the input path is a file, its parent directory is returned. If it's
        a directory, the directory itself is returned.

        Args:
            input_path: The path to resolve.

        Returns:
            A `Path` object to the source directory, or `None` if the path is invalid.
        """
        if input_path is None:
            return None
        resolved_path = input_path.resolve()
        if not resolved_path.exists():
            logger.error(f"Input path does not exist: {resolved_path}")
            return None
        if resolved_path.is_file():
            return resolved_path.parent
        elif resolved_path.is_dir():
            return resolved_path
        logger.warning(f"Input path {resolved_path} is neither a file nor a directory.")
        return None

    def set_files_to_process(self):
        """
        Abstract method to discover and set the files to be processed.

        Subclasses must implement this method to populate the `self.files`
        attribute with a tuple of `Path` objects, based on the specific file
        types they are designed to handle (e.g., video or audio extensions).
        """
        raise NotImplementedError("Subclasses must implement set_files_to_process().")

    def set_dirs_to_scan(self):
        """
        Scans the source directory recursively and populates `self.dirs`.

        This method finds all subdirectories within the `source_dir`. It respects
        the `EXCEPT_FOLDERS_KEYWORDS` configuration to avoid scanning output or
        error directories, which could lead to infinite processing loops.
        """
        if not self.source_dir:
            self.dirs = set()
            return
        def contains_excluded_keywords(path_to_check: Path) -> bool:
            return any(
                keyword.lower() in path_to_check.as_posix().lower()
                for keyword in EXCEPT_FOLDERS_KEYWORDS
            )
        discovered_dirs = set()
        if (
            self.args
            and getattr(self.args, "manual_mode", False)
            or not contains_excluded_keywords(self.source_dir)
        ):
            discovered_dirs.add(self.source_dir)
        for d_path in self.source_dir.rglob("*"):
            if d_path.is_dir():
                if (
                    self.args
                    and getattr(self.args, "manual_mode", False)
                    or not contains_excluded_keywords(d_path)
                ):
                    discovered_dirs.add(d_path.resolve())
        self.dirs = discovered_dirs
        logger.debug(
            f"Set {len(self.dirs)} directories to scan under {self.source_dir}"
        )

    def remove_empty_dirs(self):
        """
        Finds and removes all empty subdirectories within the source directory.

        This method runs iteratively to handle nested empty directories. For example,
        if `A/B/` is empty, it first removes `B`, and in the next pass, it removes `A`.
        """
        if not self.source_dir:
            return
        deleted_in_pass = True
        while deleted_in_pass:
            deleted_in_pass = False
            all_dirs_in_source = sorted(
                [d for d in self.source_dir.rglob("*") if d.is_dir()], reverse=True
            )
            for empty_candidate_dir in all_dirs_in_source:
                if not empty_candidate_dir.exists():
                    continue
                if not any(empty_candidate_dir.iterdir()):
                    try:
                        empty_candidate_dir.rmdir()
                        logger.debug(f"Removed empty directory: {empty_candidate_dir}")
                        deleted_in_pass = True
                    except OSError as e:
                        if e.errno == 2: # No such file or directory (already deleted)
                            continue
                        logger.error(
                            f"Cannot delete empty directory {empty_candidate_dir}: {e}"
                        )
                        if e.errno == 5 and hasattr(
                            self, "_handle_access_denied_for_dir_removal"
                        ):
                            self._handle_access_denied_for_dir_removal(
                                empty_candidate_dir
                            )

    @staticmethod
    def _handle_access_denied_for_dir_removal(directory: Path):
        """
        A helper method for debugging when a directory cannot be deleted.

        If `remove_empty_dirs` encounters an "Access Denied" error, this method
        is called to list the contents of the directory, which might provide
        clues as to why it's not considered empty (e.g., hidden files).
        """
        logger.warning(
            f"Access denied trying to remove {directory}. Attempting to list contents for clues."
        )
        try:
            for item in directory.iterdir():
                logger.warning(
                    f"  - Found item: {item.name} (Type: {'dir' if item.is_dir() else 'file'})"
                )
        except Exception as e:
            logger.error(
                f"Could not list contents of {directory} during access denied handling: {e}"
            )

    def delete_temp_folders(self):
        """
        Finds and deletes temporary folders created by external tools.

        This method specifically targets folders matching patterns like
        `.ab-av1-*`, which are created by the `ab-av1` tool during the
        CRF search process.
        """
        if not self.source_dir:
            return
        temp_patterns = [".ab-av1-*", ".temp*"]
        temp_dirs_found = []
        for pattern in temp_patterns:
            temp_dirs_found.extend(list(self.source_dir.rglob(pattern)))
        for temp_dir_path in temp_dirs_found:
            if temp_dir_path.is_dir():
                try:
                    shutil.rmtree(temp_dir_path, ignore_errors=False)
                    logger.info(f"Deleted temporary folder: {temp_dir_path}")
                except Exception as e:
                    logger.error(
                        f"Failed to delete temporary folder {temp_dir_path}: {e}"
                    )
        self.set_dirs_to_scan() # Rescan after deleting

    def move_raw_folder_if_no_process_files(self, destination_root: Path):
        """
        Archives a source subdirectory if it no longer contains processable files.

        This is a cleanup utility used by pipelines. After a batch of files in
        a subdirectory has been processed, this method checks if any processable
        files remain. If not, the entire subdirectory is moved to an archive
        location (`destination_root`) to signify that its contents are complete.

        Args:
            destination_root: The root directory of the archive where the
                              source subdirectory will be moved.
        """
        if not self.source_dir:
            logger.warning(
                f"Source directory not set for {self.__class__.__name__}, cannot move raw folders."
            )
            return
        destination_root = destination_root.resolve()
        dirs_to_check = self.dirs - {self.source_dir}
        for sub_dir_to_check in dirs_to_check:
            if not sub_dir_to_check.is_dir() or not sub_dir_to_check.exists():
                continue
            checker_instance = self.__class__(sub_dir_to_check, self.args)
            if not checker_instance.files: # No processable files in this specific subdir
                try:
                    relative_path_of_subdir = sub_dir_to_check.relative_to(
                        self.source_dir
                    )
                    final_target_path = destination_root / relative_path_of_subdir
                except ValueError:
                    logger.error(
                        f"Cannot determine relative path for {sub_dir_to_check} from {self.source_dir}. Skipping move."
                    )
                    continue
                logger.debug(
                    f"Directory {sub_dir_to_check.name} has no processable files. Preparing to move to {final_target_path}."
                )
                try:
                    final_target_path.parent.mkdir(parents=True, exist_ok=True)
                    if final_target_path.exists():
                        if final_target_path.is_dir():
                            logger.warning(
                                f"Target directory {final_target_path} already exists. Removing it before moving."
                            )
                            shutil.rmtree(final_target_path)
                        else:
                            logger.error(
                                f"Target path {final_target_path} exists and is a file. Cannot move directory {sub_dir_to_check.name} there. Skipping."
                            )
                            continue
                    sub_dir_to_check.rename(final_target_path)
                    logger.debug(f"Moved {sub_dir_to_check.name} to {final_target_path}")
                except Exception as e:
                    logger.error(
                        f"Error moving directory {sub_dir_to_check.name} to {final_target_path}: {e}",
                        exc_info=True,
                    )
            else:
                logger.debug(
                    f"Directory {sub_dir_to_check.name} contains processable files, not moving."
                )
        self.set_dirs_to_scan()
        self.set_files_to_process()

    def standardize_discovered_file_names(self):
        """
        Normalizes the names of all discovered files.

        This method cleans up filenames to prevent potential issues with command-line
        tools. It performs actions like:
        - Replacing multiple whitespace characters with a single space.
        - Removing specific character sets (e.g., Korean characters) that can
          cause problems in some environments.
        - The original file is renamed on the filesystem.
        """
        if not self.files:
            return

        def normalize_whitespace(text: str) -> str:
            # Replace multiple whitespace characters (including tabs, newlines) with a single space
            # Then strip leading/trailing whitespace
            return re.sub(r'\s+', ' ', text).strip()

        def remove_korean_chars(filename_part: str) -> str:
            # Removes only Korean characters, leaves other CJK characters if any
            korean_pattern = re.compile(
                r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\ua960-\ua97f\ud7b0-\ud7ff]+"
            )
            return korean_pattern.sub("", filename_part)

        updated_files_list = list(self.files) # Make a mutable copy

        for i, file_path_obj in enumerate(self.files):
            if not file_path_obj.exists(): # File might have been moved/deleted by another process
                logger.warning(f"File {file_path_obj} not found during name standardization. Skipping.")
                continue

            original_name = file_path_obj.name
            file_stem = file_path_obj.stem
            file_suffix = file_path_obj.suffix

            # Apply normalizations
            normalized_stem = normalize_whitespace(file_stem)
            normalized_stem_no_korean = remove_korean_chars(normalized_stem)

            new_file_name_candidate = normalized_stem_no_korean + file_suffix

            if new_file_name_candidate != original_name:
                new_file_path = file_path_obj.with_name(new_file_name_candidate)
                if new_file_path.resolve() == file_path_obj.resolve(): # No actual change after normalization
                    logger.trace(f"File name '{original_name}' is already standard or normalization resulted in same path. No rename needed.")
                    continue

                if new_file_path.exists():
                    logger.warning(
                        f"Cannot rename '{original_name}' to '{new_file_name_candidate}': target already exists at {new_file_path}. Skipping rename for this file."
                    )
                    continue
                try:
                    logger.info(f"Renaming file: '{file_path_obj}' to: '{new_file_path}'")
                    file_path_obj.rename(new_file_path)
                    updated_files_list[i] = new_file_path # Update the list with the new Path object
                except Exception as e:
                    logger.error(
                        f"Error renaming file '{original_name}' to '{new_file_name_candidate}': {e}"
                    )
            else:
                logger.trace(f"File name '{original_name}' requires no standardization.")


        self.files = tuple(updated_files_list) # Update the instance's file list

    def standardize_dir_names(self): # This method is complex and not directly related to the bug, keeping as is.
        """
        Normalizes the names of discovered directories.

        This method cleans up directory names by replacing characters that can
        be problematic in file paths or scripts.
        Note: Use with caution, as changing directory names during processing
        can be risky.
        """
        logger.warning(
            "standardize_dir_names is complex and might lead to issues if paths change during processing. Use with caution or as a separate utility."
        )
        if not self.dirs:
            return
        def replace_unwanted_chars_in_dir_name(dir_name_str: str) -> str:
            name_part = Path(dir_name_str).stem
            suffix_part = Path(dir_name_str).suffix
            processed_name = (
                name_part.replace(".", "_").replace("[", "(").replace("]", ")")
            )
            return processed_name + suffix_part
        current_dirs_snapshot = list(self.dirs)
        updated_dirs_set = set()
        for dir_path_obj in current_dirs_snapshot:
            if not dir_path_obj.exists():
                continue
            original_dir_name = dir_path_obj.name
            new_dir_name = replace_unwanted_chars_in_dir_name(original_dir_name)
            if new_dir_name != original_dir_name:
                new_dir_path = dir_path_obj.with_name(new_dir_name)
                if new_dir_path.exists() and new_dir_path != dir_path_obj:
                    logger.warning(
                        f"Cannot rename directory {original_dir_name} to {new_dir_name}: target exists. Merging or handling collision."
                    )
                    updated_dirs_set.add(dir_path_obj)
                    continue
                try:
                    logger.info(
                        f"Renaming directory: {dir_path_obj} to: {new_dir_path}"
                    )
                    dir_path_obj.rename(new_dir_path)
                    updated_dirs_set.add(new_dir_path)
                except Exception as e:
                    logger.error(
                        f"Error renaming directory {original_dir_name} to {new_dir_name}: {e}"
                    )
                    updated_dirs_set.add(dir_path_obj)
            else:
                updated_dirs_set.add(dir_path_obj)
        self.dirs = updated_dirs_set

    def remove_small_files(self, min_size_bytes: int = MINIMUM_FILE_SIZE):
        """
        Deletes discovered files that are smaller than a configured threshold.

        This is used to filter out invalid, empty, or corrupted files that
        are too small to be valid media files, preventing them from causing
        errors later in the pipeline.

        Args:
            min_size_bytes: The minimum file size in bytes. Files smaller
                            than this will be deleted.
        """
        if not self.files:
            return
        logger.info(
            f"Checking for files smaller than {formatted_size(min_size_bytes)} to remove."
        )
        surviving_files = []
        for file_path_obj in self.files:
            if not file_path_obj.exists():
                continue
            try:
                current_size = file_path_obj.stat().st_size
                if current_size < min_size_bytes:
                    file_path_obj.unlink(missing_ok=True)
                    logger.info(
                        f"Deleted small file: {file_path_obj.name} ({formatted_size(current_size)})"
                    )
                else:
                    surviving_files.append(file_path_obj)
            except FileNotFoundError:
                logger.warning(
                    f"File {file_path_obj.name} was not found during small file check (possibly deleted by another process)."
                )
            except Exception as e:
                logger.error(
                    f"Error checking/deleting small file {file_path_obj.name}: {e}"
                )
                surviving_files.append(file_path_obj)
        self.files = tuple(surviving_files)
        logger.info(f"{len(self.files)} files remain after small file check.")


class ProcessVideoFiles(ProcessFiles):
    """
    A concrete file processor that discovers video files.

    This class implements `set_files_to_process` to specifically look for
    files with extensions listed in the `VIDEO_EXTENSIONS` config.
    """
    def set_files_to_process(self):
        """
        Scans the directories and populates `self.files` with video files.
        """
        if not self.dirs:
            self.files = tuple()
            return
        discovered_video_files = []
        for d_path in self.dirs:
            if d_path.is_dir():
                for ext in VIDEO_EXTENSIONS:
                    discovered_video_files.extend(list(d_path.glob(f"*{ext}")))
        self.files = tuple(sorted(list(set(discovered_video_files))))
        logger.debug(f"ProcessVideoFiles: Discovered {len(self.files)} video files.")


class ProcessPhoneFiles(ProcessVideoFiles):
    """

    Extends `ProcessVideoFiles` with logic to avoid reprocessing completed files.

    This class is used by the `PhoneEncodingPipeline`. In addition to finding
    video files, it reads the main success log and excludes any files that
    have already been successfully processed in previous runs.
    """
    def set_files_to_process(self):
        """
        Discovers video files and then filters out any that are already logged as complete.
        """
        super().set_files_to_process()
        success_log_path = Path.cwd() / DEFAULT_SUCCESS_LOG_YAML
        processed_file_stems = set()
        if success_log_path.is_file():
            try:
                with success_log_path.open("r", encoding="utf-8") as f:
                    success_log_list = yaml.safe_load(f)
                if isinstance(success_log_list, list):
                    for entry in success_log_list:
                        if isinstance(entry, dict) and "input_file" in entry:
                            processed_file_stems.add(Path(entry["input_file"]).stem)
            except yaml.YAMLError as e:
                logger.error(f"Error parsing success log {success_log_path}: {e}")
            except Exception as e:
                logger.error(
                    f"Unexpected error reading success log {success_log_path}: {e}"
                )
        if processed_file_stems:
            original_file_count = len(self.files)
            self.files = tuple(
                f for f in self.files if f.stem not in processed_file_stems
            )
            logger.info(
                f"ProcessPhoneFiles: Excluded {original_file_count - len(self.files)} files already in success log. {len(self.files)} remaining."
            )
        else:
            logger.debug(
                f"ProcessPhoneFiles: No success log found or no files to exclude. {len(self.files)} video files."
            )


class ProcessAudioFiles(ProcessFiles):
    """
    A concrete file processor that discovers audio files.

    This class implements `set_files_to_process` to specifically look for
    files with extensions listed in the `AUDIO_EXTENSIONS` config.
    """
    def set_files_to_process(self):
        """
        Scans the directories and populates `self.files` with audio files.
        """
        if not self.dirs:
            self.files = tuple()
            return
        discovered_audio_files = []
        for d_path in self.dirs:
            if d_path.is_dir():
                for ext in AUDIO_EXTENSIONS:
                    discovered_audio_files.extend(list(d_path.glob(f"*{ext}")))
        self.files = tuple(sorted(list(set(discovered_audio_files))))
        logger.debug(f"ProcessAudioFiles: Discovered {len(self.files)} audio files.")


def cleanup_empty_error_dirs(base_path: Path):
    """
    Removes subdirectories within the main error directory if they are empty.

    This function is a final cleanup step. It scans the main application error
    directory (e.g., 'encode_error/'). If any of its subdirectories (like
    'load_failed/', 'no_audio_found/') no longer contain any files, this
    function will delete them. It also removes the main error directory itself
    if it becomes completely empty.

    Args:
        base_path: The root project path from which to find the error directory.
    """
    # BASE_ERROR_DIR might be resolved at import time. Use its name to construct a path
    # relative to the determined project_path for consistency.
    error_dir = base_path / BASE_ERROR_DIR.name

    if not error_dir.is_dir():
        logger.debug(f"Error directory not found at '{error_dir}', skipping cleanup.")
        return

    logger.info(f"Starting cleanup of empty error directories in '{error_dir}'...")

    video_extensions_lower = {ext.lower() for ext in VIDEO_EXTENSIONS}

    # Iterate through subdirectories of the error directory
    # Note: Use list() to create a snapshot, as we might be deleting from the directory we are iterating.
    subdirs_to_check = [d for d in error_dir.iterdir() if d.is_dir()]

    for sub_dir in subdirs_to_check:
        has_video_file = False
        # Recursively check for video files in the subdirectory
        for file_path in sub_dir.rglob('*'):
            if file_path.is_file() and file_path.suffix.lower() in video_extensions_lower:
                has_video_file = True
                logger.trace(f"Found video file in '{sub_dir.name}', skipping deletion: {file_path.name}")
                break  # Found one, no need to check further in this subdir

        if not has_video_file:
            logger.info(f"No video files found in '{sub_dir}'. Deleting directory.")
            try:
                shutil.rmtree(sub_dir)
                logger.debug(f"Successfully deleted directory: '{sub_dir}'")
            except OSError as e:
                logger.error(f"Failed to delete directory '{sub_dir}': {e}")

    # After cleaning subdirectories, check if the main error directory is now empty
    try:
        # Check if the directory is empty. any(error_dir.iterdir()) is a good way.
        if not any(error_dir.iterdir()):
            logger.info(f"Error directory '{error_dir}' is now empty after cleanup. Deleting it.")
            error_dir.rmdir()
            logger.debug(f"Successfully deleted empty error directory: '{error_dir}'")
    except FileNotFoundError:
        # This can happen if another process deletes it.
        pass
    except OSError as e:
        # This can happen if the directory contains hidden files etc. that we didn't account for.
        logger.warning(f"Could not remove final error directory '{error_dir}': {e}")