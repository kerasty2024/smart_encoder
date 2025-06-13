"""
Defines data models for temporary state management, primarily for tracking
encoding job progress and status.
"""

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from ..config.common import JOB_STATUS_PENDING


class EncodeInfo:
    """
    Manages information and state for a single encoding job.

    This class is crucial for making the encoding process restartable and resilient.
    It serializes the state of an encoding task to a YAML file, which acts as a
    progress tracker. The YAML file is typically named after the MD5 hash of the
    source media file (e.g., `[hash].progress.yaml`) and stored in a cache
    directory within the output folder.

    Lifecycle:
    1. When an encoding task starts, it creates an `EncodeInfo` instance for the file.
    2. It calls `load()` to check for an existing progress file.
       - If a file is found with status 'completed', the task can be skipped.
       - If the status is 'error_retryable', the `retry_count` is checked.
       - If no file exists, a new one is created with 'pending' status.
    3. Throughout the pipeline (preprocessing, encoding), the status and other relevant
       data are updated by calling the `dump()` method.
    4. Once a job is finished (completed, skipped, or failed permanently), the
       `remove_file()` method is called to clean up the progress file.

    Attributes:
        file_hash (str): The MD5 hash of the source media file, used as the primary identifier.
        path (Path): The full path to the `.progress.yaml` file on the filesystem.
        storage_dir (Path): The directory where the progress file is stored.
        status (str): The current stage of the job. See `src.config.common` for job status constants
                      (e.g., 'pending', 'preprocessing_done', 'completed', 'error_retryable').
        retry_count (int): How many times a failed job has been attempted.
        encoder (str): The video encoder being used (e.g., 'libsvtav1').
        crf (int): The Constant Rate Factor (CRF) value determined for the encode.
        ori_video_path (Optional[str]): The original path of the source media file.
        ffmpeg_command (Optional[str]): The last FFmpeg command that was executed or will be executed.
        temp_output_path (Optional[str]): The path to the temporary or final encoded output file.
        last_error_message (Optional[str]): A brief message describing the last error encountered.
        last_updated (Optional[str]): ISO 8601 timestamp of when the progress file was last saved.
        pre_encoder_data (Optional[dict]): A dictionary holding results from the preprocessing stage,
                                          such as selected streams or CRF search results.
    """

    def __init__(self, file_hash: str, encoder: str = "", crf: int = 0, storage_dir: Path = Path(".")):
        """
        Initializes the EncodeInfo instance for a specific file.

        Args:
            file_hash: The unique hash (e.g., MD5) of the media file this info pertains to.
                       This cannot be empty.
            encoder: The default encoder if known at initialization.
            crf: The default CRF value if known at initialization.
            storage_dir: The directory where the YAML progress file will be stored.
                         A subdirectory '.encode_info_cache' is typically used.
        """
        if not file_hash:
            raise ValueError("file_hash cannot be empty for EncodeInfo.")
        self.file_hash = file_hash
        self.encoder = encoder
        self.crf = crf
        self.storage_dir = storage_dir.resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / f"{self.file_hash}.progress.yaml"
        self.ori_video_path: Optional[str] = None

        self.status: str = JOB_STATUS_PENDING
        self.ffmpeg_command: Optional[str] = None
        self.temp_output_path: Optional[str] = None
        self.last_error_message: Optional[str] = None
        self.retry_count: int = 0
        self.last_updated: Optional[str] = datetime.now().isoformat()
        self.pre_encoder_data: Optional[dict] = None

    def dump(self,
             status: Optional[str] = None,
             encoder: Optional[str] = None,
             crf: Optional[int] = None,
             ori_video_path: Optional[str] = None,
             ffmpeg_command: Optional[str] = None,
             temp_output_path: Optional[str] = None,
             last_error_message: Optional[str] = None,
             increment_retry_count: bool = False,
             pre_encoder_data: Optional[dict] = None
             ):
        """
        Updates instance attributes and saves the current state to the YAML file.

        This method is the primary way to persist the progress of an encoding job.
        Any provided arguments will overwrite the existing instance attributes before
        the state is written to the file. The `last_updated` timestamp is always
        refreshed on every dump.

        Args:
            status: New job status (e.g., 'preprocessing_done').
            encoder: The encoder being used.
            crf: The CRF value being used.
            ori_video_path: Path to the original source file.
            ffmpeg_command: The ffmpeg command string for the current job.
            temp_output_path: Path to the temporary or final output file.
            last_error_message: The last error encountered.
            increment_retry_count: If True, increments the `retry_count` by 1.
            pre_encoder_data: A dictionary of results from the pre-encoding stage.
        """
        if status: self.status = status
        if encoder: self.encoder = encoder
        if crf is not None: self.crf = crf
        if ori_video_path: self.ori_video_path = ori_video_path
        if ffmpeg_command: self.ffmpeg_command = ffmpeg_command
        if temp_output_path: self.temp_output_path = temp_output_path
        if last_error_message: self.last_error_message = last_error_message
        if increment_retry_count: self.retry_count += 1
        if pre_encoder_data: self.pre_encoder_data = pre_encoder_data

        self.last_updated = datetime.now().isoformat()

        dump_dict = {
            "file_hash": self.file_hash,
            "ori_video_path": self.ori_video_path,
            "status": self.status,
            "encoder": self.encoder,
            "crf": self.crf,
            "ffmpeg_command": self.ffmpeg_command,
            "temp_output_path": self.temp_output_path,
            "last_error_message": self.last_error_message,
            "retry_count": self.retry_count,
            "last_updated": self.last_updated,
            "pre_encoder_data": self.pre_encoder_data,
        }

        try:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.dump(dump_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
        except Exception as e:
            print(f"Error dumping EncodeInfo to {self.path}: {e}")

    def load(self) -> bool:
        """
        Loads job state from the YAML file if it exists, populating the instance attributes.

        This is called at the start of a job to check if there's progress to resume from.

        Returns:
            True if the file was successfully found, loaded, and parsed.
            False if the file does not exist or an error occurred during loading/parsing.
        """
        if self.path.exists() and self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    obj_dict = yaml.safe_load(f)
                if obj_dict:
                    self.ori_video_path = obj_dict.get("ori_video_path")
                    self.status = obj_dict.get("status", JOB_STATUS_PENDING)
                    self.encoder = obj_dict.get("encoder", "")
                    self.crf = obj_dict.get("crf", 0)
                    self.ffmpeg_command = obj_dict.get("ffmpeg_command")
                    self.temp_output_path = obj_dict.get("temp_output_path")
                    self.last_error_message = obj_dict.get("last_error_message")
                    self.retry_count = obj_dict.get("retry_count", 0)
                    self.last_updated = obj_dict.get("last_updated")
                    self.pre_encoder_data = obj_dict.get("pre_encoder_data")
                    return True
            except yaml.YAMLError as e:
                print(f"Error loading or parsing EncodeInfo from {self.path}: {e}")
            except Exception as e:
                print(f"Unexpected error loading EncodeInfo from {self.path}: {e}")
        return False

    def remove_file(self):
        """
        Removes the YAML progress file from the filesystem.

        This should be called when a job is permanently finished (e.g., successfully
        completed, skipped, or failed after all retries) to clean up the cache.
        """
        try:
            if self.path.exists() and self.path.is_file():
                self.path.unlink()
        except OSError as e:
            print(f"Error removing EncodeInfo file {self.path}: {e}")