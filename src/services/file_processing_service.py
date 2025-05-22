import re
import shutil
from pathlib import Path
from typing import Set, Tuple, Any, Optional  # Added Any

import yaml
from loguru import logger

# Config
from ..config.audio import AUDIO_EXTENSIONS
from ..config.common import DEFAULT_SUCCESS_LOG_YAML, MINIMUM_FILE_SIZE
from ..config.video import EXCEPT_FOLDERS_KEYWORDS, VIDEO_EXTENSIONS
from ..utils.format_utils import formatted_size


class ProcessFiles:
    dirs: Set[Path] = set()
    files: Tuple[Path, ...] = tuple()
    source_dir: Path | None = None
    args: Any  # To store command line arguments

    def __init__(self, path: Path, args: Optional[Any] = None):
        self.args = args
        self.source_dir = self._get_source_directory_from_path(path)

        if self.source_dir is None:
            logger.warning(
                f"No valid source file/directory found for path: {path}. Processing will be skipped."
            )
            return

        self.set_dirs_to_scan()

        self.set_files_to_process()

        if (
            not getattr(self.args, "not_rename", False) and self.files
        ):  # Only rename if files exist
            self.standardize_discovered_file_names()

    @staticmethod
    def _get_source_directory_from_path(input_path: Path) -> Path | None:
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
        raise NotImplementedError("Subclasses must implement set_files_to_process().")

    def set_dirs_to_scan(self):
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
        ):  # Check if args exists
            discovered_dirs.add(self.source_dir)

        for d_path in self.source_dir.rglob("*"):
            if d_path.is_dir():
                if (
                    self.args
                    and getattr(self.args, "manual_mode", False)
                    or not contains_excluded_keywords(d_path)
                ):  # Check if args exists
                    discovered_dirs.add(d_path.resolve())

        self.dirs = discovered_dirs
        logger.debug(
            f"Set {len(self.dirs)} directories to scan under {self.source_dir}"
        )

    def remove_empty_dirs(self):
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
                        logger.info(f"Removed empty directory: {empty_candidate_dir}")
                        deleted_in_pass = True
                    except OSError as e:
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

        self.set_dirs_to_scan()

    def move_raw_folder_if_no_process_files(self, destination_root: Path):
        if not self.source_dir:
            logger.warning(
                f"Source directory not set for {self.__class__.__name__}, cannot move raw folders."
            )
            return

        destination_root = destination_root.resolve()
        # destination_root.mkdir(parents=True, exist_ok=True) # Parent of final dest path will be created later

        dirs_to_check = self.dirs - {self.source_dir}

        for sub_dir_to_check in dirs_to_check:
            if not sub_dir_to_check.is_dir() or not sub_dir_to_check.exists():
                continue

            checker_instance = self.__class__(sub_dir_to_check, self.args)

            if not checker_instance.files:
                try:
                    relative_path_of_subdir = sub_dir_to_check.relative_to(
                        self.source_dir
                    )
                    # final_target_path is the path the sub_dir_to_check should have *after* the move.
                    final_target_path = destination_root / relative_path_of_subdir
                except ValueError:
                    logger.error(
                        f"Cannot determine relative path for {sub_dir_to_check} from {self.source_dir}. Skipping move."
                    )
                    continue

                logger.info(
                    f"Directory {sub_dir_to_check.name} has no processable files. Preparing to move to {final_target_path}."
                )

                try:
                    # Ensure the parent directory of the final target path exists
                    final_target_path.parent.mkdir(parents=True, exist_ok=True)

                    if final_target_path.exists():
                        if final_target_path.is_dir():
                            logger.warning(
                                f"Target directory {final_target_path} already exists. Removing it before moving."
                            )
                            shutil.rmtree(final_target_path)
                        else:  # It's a file, cannot move a dir on top of a file
                            logger.error(
                                f"Target path {final_target_path} exists and is a file. Cannot move directory {sub_dir_to_check.name} there. Skipping."
                            )
                            continue

                    # Now, final_target_path either does not exist, or was a dir and got removed.
                    # We can use rename which is atomic on the same filesystem.
                    sub_dir_to_check.rename(final_target_path)
                    logger.info(f"Moved {sub_dir_to_check.name} to {final_target_path}")

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
        if not self.files:
            return

        def remove_korean_chars_and_normalize(filename: str) -> str:
            korean_pattern = re.compile(
                r"[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f\u3200-\u321f\u3260-\u327f\uffa0-\uffdf\ua960-\ua97f\ud7b0-\ud7ff]+"
            )
            normalized_name = re.sub(r"\s+", " ", filename).strip()
            return korean_pattern.sub("", normalized_name)

        updated_files_list = list(self.files)

        for i, file_path_obj in enumerate(self.files):
            if not file_path_obj.exists():
                continue

            original_name = file_path_obj.name
            new_file_name = remove_korean_chars_and_normalize(original_name)

            if new_file_name != original_name:
                new_file_path = file_path_obj.with_name(new_file_name)
                if new_file_path.exists() and new_file_path != file_path_obj:
                    logger.warning(
                        f"Cannot rename {original_name} to {new_file_name}: target already exists at {new_file_path}. Skipping rename."
                    )
                    continue
                try:
                    logger.info(f"Renaming file: {file_path_obj} to: {new_file_path}")
                    file_path_obj.rename(new_file_path)
                    updated_files_list[i] = new_file_path
                except Exception as e:
                    logger.error(
                        f"Error renaming file {original_name} to {new_file_name}: {e}"
                    )

        self.files = tuple(updated_files_list)

    def standardize_dir_names(self):
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
                # Get size once
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
    def set_files_to_process(self):
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
    def set_files_to_process(self):
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
    def set_files_to_process(self):
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