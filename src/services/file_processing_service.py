import re
import shutil
from pathlib import Path
from typing import Set, Tuple  # For type hinting

import yaml
from loguru import logger

# Config
from ..config.audio import AUDIO_EXTENSIONS
from ..config.common import DEFAULT_SUCCESS_LOG_YAML, MINIMUM_FILE_SIZE
from ..config.video import EXCEPT_FOLDERS_KEYWORDS, VIDEO_EXTENSIONS


class ProcessFiles:
    """
    Base class for discovering and managing files to be processed.
    Subclasses define specific file types (video, audio, etc.).
    """

    # Use Set for dirs and Tuple for files for type hinting clarity
    dirs: Set[Path] = set()
    files: Tuple[Path, ...] = tuple()
    source_dir: Path | None = None  # Root directory for this processing instance

    def __init__(self, path: Path, args=None):  # path should be mandatory
        self.args = args
        self.source_dir = self._get_source_directory_from_path(path)

        if self.source_dir is None:
            logger.warning(
                f"No valid source file/directory found for path: {path}. Processing will be skipped."
            )
            return  # Further initialization depends on source_dir

        self.set_dirs_to_scan()  # Discover directories based on source_dir and args

        # Standardize names before discovering files if not disabled
        if not getattr(self.args, "not_rename", False):
            # Standardizing directory names can change paths, so rescan dirs if needed
            # self.standardize_dir_names() # This can be problematic if dirs set changes during iteration
            # For simplicity, standardize file names on currently found files
            # It's safer to standardize names in a separate preliminary step if dir names also change.
            # Assuming standardize_file_names operates on a snapshot of files from set_files.
            pass  # Moved standardize_file_names to after set_files to operate on discovered files

        self.set_files_to_process()  # Discover actual files based on extensions and dirs

        if not getattr(self.args, "not_rename", False):
            self.standardize_discovered_file_names()  # Now rename the discovered files

    @staticmethod
    def _get_source_directory_from_path(input_path: Path) -> Path | None:
        """
        Determines the absolute source directory from the given input path.
        If input_path is a file, its parent directory is returned.
        If input_path is a directory, it's returned directly.
        Returns None if the path is invalid or does not exist.
        """
        if input_path is None:
            return None

        resolved_path = input_path.resolve()  # Work with absolute paths

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
        Abstract method to be overridden by subclasses.
        This method should populate `self.files` with Path objects of files to be processed.
        """
        raise NotImplementedError("Subclasses must implement set_files_to_process().")

    def set_dirs_to_scan(self):
        """
        Sets directories for processing, starting from `self.source_dir`.
        Excludes directories containing specified keywords if not in manual mode.
        """
        if not self.source_dir:
            self.dirs = set()
            return

        def contains_excluded_keywords(path_to_check: Path) -> bool:
            # EXCEPT_FOLDERS_KEYWORDS should be from config.video
            return any(
                keyword.lower()
                in path_to_check.as_posix().lower()  # Case-insensitive check
                for keyword in EXCEPT_FOLDERS_KEYWORDS
            )

        discovered_dirs = set()
        # Add source_dir itself if it's not excluded (manual_mode overrides exclusion)
        if self.args.manual_mode or not contains_excluded_keywords(self.source_dir):
            discovered_dirs.add(self.source_dir)

        # Glob for subdirectories
        for d_path in self.source_dir.rglob(
            "*"
        ):  # rglob finds all sub-items recursively
            if d_path.is_dir():
                if self.args.manual_mode or not contains_excluded_keywords(d_path):
                    discovered_dirs.add(d_path.resolve())

        self.dirs = discovered_dirs
        logger.debug(
            f"Set {len(self.dirs)} directories to scan under {self.source_dir}"
        )

    def remove_empty_dirs(self):
        """Removes empty directories within `self.source_dir` recursively."""
        if not self.source_dir:
            return

        # Iterate multiple times if needed, as removing one dir might make its parent empty
        deleted_in_pass = True
        while deleted_in_pass:
            deleted_in_pass = False
            # Iterate from deeper paths to shallower ones to correctly identify empty parents
            # sorted list of Path objects, reverse=True means deeper paths first
            all_dirs_in_source = sorted(
                [d for d in self.source_dir.rglob("*") if d.is_dir()], reverse=True
            )

            for empty_candidate_dir in all_dirs_in_source:
                if not empty_candidate_dir.exists():  # Already deleted
                    continue
                if not any(empty_candidate_dir.iterdir()):  # Check if truly empty
                    try:
                        empty_candidate_dir.rmdir()
                        logger.info(f"Removed empty directory: {empty_candidate_dir}")
                        deleted_in_pass = True
                    except OSError as e:
                        # Common errors:
                        # errno 2: No such file or directory (already deleted by another process/thread)
                        # errno 5: Access denied (permissions issue)
                        # errno 39: Directory not empty (race condition or hidden files)
                        # errno 145 (Windows): Directory not empty (often due to open handles)
                        if e.errno == 2:
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
        Attempts to handle access denied errors during directory removal (Windows specific focus).
        This is a basic attempt; robust handling is complex.
        """
        logger.warning(
            f"Access denied trying to remove {directory}. Attempting to list contents for clues."
        )
        try:
            for item in directory.iterdir():  # Try to see what's inside
                logger.warning(
                    f"  - Found item: {item.name} (Type: {'dir' if item.is_dir() else 'file'})"
                )
            # On Windows, chmod might not be as effective as Linux for open handles.
            # directory.chmod(0o777) # Less likely to help with "in use" errors
        except Exception as e:
            logger.error(
                f"Could not list contents of {directory} during access denied handling: {e}"
            )

    def delete_temp_folders(self):
        """Deletes temporary folders matching common patterns within `self.source_dir`."""
        if not self.source_dir:
            return

        temp_patterns = [".ab-av1-*", ".temp*"]  # From original
        temp_dirs_found = []
        for pattern in temp_patterns:
            # Search within self.source_dir and its subdirectories
            temp_dirs_found.extend(list(self.source_dir.rglob(pattern)))

        for temp_dir_path in temp_dirs_found:
            if temp_dir_path.is_dir():  # Ensure it's a directory
                try:
                    shutil.rmtree(
                        temp_dir_path, ignore_errors=False
                    )  # ignore_errors=False to see issues
                    logger.info(f"Deleted temporary folder: {temp_dir_path}")
                except Exception as e:
                    logger.error(
                        f"Failed to delete temporary folder {temp_dir_path}: {e}"
                    )

        # After deleting temp folders, the set of valid directories might change
        self.set_dirs_to_scan()  # Refresh the list of directories

    def move_raw_folder_if_no_process_files(self, destination_root: Path):
        """
        Moves subdirectories from `self.source_dir` to `destination_root` if they
        contain no files matching the criteria of *this ProcessFiles instance type*.
        The check for "no process files" is specific to the subclass (Video, Audio, etc.).
        """
        if not self.source_dir:
            logger.warning(
                f"Source directory not set for {self.__class__.__name__}, cannot move raw folders."
            )
            return

        destination_root = destination_root.resolve()
        destination_root.mkdir(parents=True, exist_ok=True)

        # Iterate over a snapshot of self.dirs, excluding source_dir itself from being moved.
        # Directories are checked one by one.
        # self.dirs contains all relevant dirs under self.source_dir (including self.source_dir).
        dirs_to_check = self.dirs - {self.source_dir}

        for sub_dir_to_check in dirs_to_check:
            if (
                not sub_dir_to_check.is_dir() or not sub_dir_to_check.exists()
            ):  # Might have been deleted or moved
                continue

            # Create a temporary ProcessFiles instance of the *same type* as self (e.g., ProcessVideoFiles)
            # to check for relevant files *within* this specific sub_dir_to_check.
            # Pass current args.
            checker_instance = self.__class__(sub_dir_to_check, self.args)

            if (
                not checker_instance.files
            ):  # If this specific dir has no processable files of this type
                # Calculate target path under destination_root, preserving relative structure from self.source_dir
                try:
                    relative_path_of_subdir = sub_dir_to_check.relative_to(
                        self.source_dir
                    )
                    target_move_path = destination_root / relative_path_of_subdir
                except ValueError:  # sub_dir_to_check is not under self.source_dir (should not happen with rglob)
                    logger.error(
                        f"Cannot determine relative path for {sub_dir_to_check} from {self.source_dir}. Skipping move."
                    )
                    continue

                logger.info(
                    f"Directory {sub_dir_to_check.name} has no processable files. Moving to {target_move_path.parent}."
                )
                target_move_path.parent.mkdir(parents=True, exist_ok=True)

                try:
                    # shutil.move can move a dir. If target_move_path exists as dir, it might merge or error.
                    # For safety, ensure target_move_path does not exist or use copytree + rmtree.
                    if target_move_path.exists():
                        logger.warning(
                            f"Target move path {target_move_path} already exists. Merging/overwriting."
                        )
                        # shutil.copytree(sub_dir_to_check, target_move_path, dirs_exist_ok=True)
                        # shutil.rmtree(sub_dir_to_check)
                        # Safer: move to a unique name if target exists, or skip. For now, default move.
                    shutil.move(str(sub_dir_to_check), str(target_move_path))
                    logger.info(f"Moved {sub_dir_to_check} to {target_move_path}")
                except Exception as e:
                    logger.error(
                        f"Error moving directory {sub_dir_to_check} to {target_move_path}: {e}"
                    )
            else:
                logger.debug(
                    f"Directory {sub_dir_to_check.name} contains processable files, not moving."
                )

        # After potential moves, refresh self.dirs and self.files
        self.set_dirs_to_scan()
        self.set_files_to_process()

    def standardize_discovered_file_names(self):
        """
        Renames files in `self.files` to remove unwanted characters (e.g., Korean).
        This method operates on the currently discovered `self.files`.
        """
        if not self.files:  # No files discovered yet
            return

        def remove_korean_chars_and_normalize(filename: str) -> str:
            # Korean character ranges (Hangul Syllables, Jamo, Compatibility Jamo, etc.)
            # Plus some common CJK punctuation that might be mixed in.
            korean_pattern = re.compile(
                r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\u3200-\u321f\u3260-\u327f\uffa0-\uffdf\ua960-\ua97f\ud7b0-\ud7ff]+"
                # Add other characters/patterns to remove or replace as needed
            )
            # Normalize spaces, remove multiple spaces
            normalized_name = re.sub(r"\s+", " ", filename).strip()
            return korean_pattern.sub("", normalized_name)

        updated_files_list = list(
            self.files
        )  # Create a mutable copy to update paths if renamed

        for i, file_path_obj in enumerate(self.files):
            if (
                not file_path_obj.exists()
            ):  # File might have been moved/deleted by another process
                continue

            original_name = file_path_obj.name
            new_file_name = remove_korean_chars_and_normalize(original_name)

            if new_file_name != original_name:
                new_file_path = file_path_obj.with_name(new_file_name)
                # Check for collision if new_file_path already exists
                if new_file_path.exists() and new_file_path != file_path_obj:
                    logger.warning(
                        f"Cannot rename {original_name} to {new_file_name}: target already exists at {new_file_path}. Skipping rename."
                    )
                    continue
                try:
                    logger.info(f"Renaming file: {file_path_obj} to: {new_file_path}")
                    file_path_obj.rename(new_file_path)
                    updated_files_list[i] = new_file_path  # Update path in our list
                except Exception as e:
                    logger.error(
                        f"Error renaming file {original_name} to {new_file_name}: {e}"
                    )

        self.files = tuple(
            updated_files_list
        )  # Update self.files with potentially new paths

    def standardize_dir_names(self):
        """
        Renames directories within `self.dirs` to replace unwanted characters.
        Warning: Modifying directory names while iterating or relying on stable paths is complex.
        This should ideally be a separate pre-processing step if used.
        """
        logger.warning(
            "standardize_dir_names is complex and might lead to issues if paths change during processing. Use with caution or as a separate utility."
        )
        if not self.dirs:
            return

        def replace_unwanted_chars_in_dir_name(dir_name_str: str) -> str:
            # Example: replace dots (not extension dots), brackets
            # This is highly dependent on specific needs.
            name_part = Path(
                dir_name_str
            ).stem  # Get name without final extension (if any)
            suffix_part = Path(dir_name_str).suffix  # Get final extension (if any)

            # Be careful with replacing "." as it's part of file extensions and hidden files.
            # Assuming we only want to replace them in the main name part.
            processed_name = (
                name_part.replace(".", "_").replace("[", "(").replace("]", ")")
            )
            return processed_name + suffix_part

        # Iterate over a snapshot of dirs. Renaming can invalidate paths.
        # This loop needs to be robust if dirs are nested and parent names change.
        # A better approach is often top-down or a dedicated utility run *before* file processing.

        # For now, simple iteration on a copy. This won't handle nested renames well in one pass.
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
                    # Collision handling: shutil.copytree(dir_path_obj, new_dir_path, dirs_exist_ok=True) then rmtree(dir_path_obj)
                    # Or skip. For now, skip.
                    updated_dirs_set.add(
                        dir_path_obj
                    )  # Keep original if rename fails due to collision
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
                    updated_dirs_set.add(dir_path_obj)  # Keep original on error
            else:
                updated_dirs_set.add(dir_path_obj)  # No change

        self.dirs = updated_dirs_set
        # Crucially, after renaming directories, self.files (if already populated) would be invalid.
        # This is why set_files should run *after* all directory path finalizations.
        # Or, file paths need to be reconstructed.

    def remove_small_files(self, min_size_bytes: int = MINIMUM_FILE_SIZE):
        """Removes files from `self.files` that are smaller than `min_size_bytes`."""
        if not self.files:
            return

        logger.info(
            f"Checking for files smaller than {formatted_size(min_size_bytes)} to remove."
        )
        surviving_files = []
        for file_path_obj in self.files:
            if not file_path_obj.exists():
                continue  # Already gone

            try:
                if file_path_obj.stat().st_size < min_size_bytes:
                    file_path_obj.unlink(
                        missing_ok=True
                    )  # missing_ok if another process deleted it
                    logger.info(
                        f"Deleted small file: {file_path_obj.name} ({formatted_size(file_path_obj.stat().st_size if file_path_obj.exists() else 0)})"
                    )
                else:
                    surviving_files.append(file_path_obj)
            except FileNotFoundError:  # Race condition: file deleted between .exists() and .stat() or .unlink()
                logger.warning(
                    f"File {file_path_obj.name} was not found during small file check (possibly deleted by another process)."
                )
            except Exception as e:
                logger.error(
                    f"Error checking/deleting small file {file_path_obj.name}: {e}"
                )
                surviving_files.append(
                    file_path_obj
                )  # Keep if error during check/delete

        self.files = tuple(surviving_files)
        logger.info(f"{len(self.files)} files remain after small file check.")


# --- Subclasses for specific file types ---


class ProcessVideoFiles(ProcessFiles):
    def set_files_to_process(self):
        """Overrides to set files with video extensions from `self.dirs`."""
        if not self.dirs:
            self.files = tuple()
            return

        discovered_video_files = []
        for d_path in self.dirs:  # self.dirs should be up-to-date
            if d_path.is_dir():  # Ensure it's still a directory
                for ext in VIDEO_EXTENSIONS:  # VIDEO_EXTENSIONS from config.video
                    discovered_video_files.extend(
                        list(d_path.glob(f"*{ext}"))
                    )  # Case-sensitive glob
                    # For case-insensitive, would need more complex logic or multiple globs
                    # e.g., d_path.glob(f"*[.{ext.lower().lstrip('.')}]") + d_path.glob(f"*[.{ext.upper().lstrip('.')}]")
                    # but VIDEO_EXTENSIONS usually are lowercase.

        # Sort for consistent processing order (optional but good practice)
        self.files = tuple(
            sorted(list(set(discovered_video_files)))
        )  # set to remove duplicates if globs overlap
        logger.debug(f"ProcessVideoFiles: Discovered {len(self.files)} video files.")


class ProcessPhoneFiles(ProcessVideoFiles):  # Inherits video file discovery
    def set_files_to_process(self):
        """
        Overrides to set video files, excluding those already listed in a success log.
        (Typically used for iPhone video pipeline to avoid re-processing).
        """
        super().set_files_to_process()  # Get all video files first

        # DEFAULT_SUCCESS_LOG_YAML is from config.common
        # This assumes the success log is in the CWD, which might not always be the case.
        # Consider making log path configurable or relative to source_dir.
        # For now, use CWD as per original logic.
        success_log_path = Path.cwd() / DEFAULT_SUCCESS_LOG_YAML
        processed_file_stems = set()

        if success_log_path.is_file():
            try:
                with success_log_path.open("r", encoding="utf-8") as f:
                    success_log_list = yaml.safe_load(f)
                if isinstance(success_log_list, list):  # Ensure it's a list of entries
                    for entry in success_log_list:
                        if isinstance(entry, dict) and "input_file" in entry:
                            # Assuming "input_file" stores a path string
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
    def set_files_to_process(self):
        """Overrides to set files with audio extensions from `self.dirs`."""
        if not self.dirs:
            self.files = tuple()
            return

        discovered_audio_files = []
        for d_path in self.dirs:
            if d_path.is_dir():
                for ext in AUDIO_EXTENSIONS: # AUDIO_EXTENSIONS from config.audio
                    discovered_audio_files.extend(list(d_path.glob(f"*{ext}")))

        self.files = tuple(sorted(list(set(discovered_audio_files))))
        logger.debug(f"ProcessAudioFiles: Discovered {len(self.files)} audio files.")