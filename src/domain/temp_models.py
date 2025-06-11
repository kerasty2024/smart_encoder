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
    Manages information about an encoding job for a single file.

    This class handles the creation, loading, and updating of a YAML file
    that stores the state of an encoding task. This allows the application
    to resume interrupted jobs or skip already completed ones. The state
    file is named after the hash of the original media file.

    Attributes:
        file_hash (str): The hash (e.g., MD5) of the media file.
        path (Path): The path to the YAML progress file.
        status (str): The current status of the job (e.g., 'pending', 'completed').
        retry_count (int): The number of times this job has been retried.
        ... and other job-specific attributes.
    """
    def __init__(self, file_hash: str, encoder: str = "", crf: int = 0, storage_dir: Path = Path(".")):
        """
        Initializes the EncodeInfo instance.

        Args:
            file_hash: The hash of the media file this info pertains to.
            encoder: The default encoder if known.
            crf: The default CRF value if known.
            storage_dir: Directory where the YAML progress file will be stored.
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
        Updates attributes and saves the current state to the YAML file.

        Any provided arguments will overwrite the existing instance attributes before
        the state is written to the file.

        Args:
            status: New job status.
            encoder: Encoder being used.
            crf: CRF value being used.
            ori_video_path: Path to the original source file.
            ffmpeg_command: The ffmpeg command string for the current job.
            temp_output_path: Path to the temporary output file.
            last_error_message: The last error encountered.
            increment_retry_count: If True, increments the retry counter.
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
        Loads job state from the YAML file if it exists.

        Returns:
            True if the file was successfully loaded and parsed, otherwise False.
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
        """Removes the YAML progress file from the filesystem."""
        try:
            if self.path.exists() and self.path.is_file():
                self.path.unlink()
        except OSError as e:
            print(f"Error removing EncodeInfo file {self.path}: {e}")