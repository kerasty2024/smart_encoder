# src/domain/temp_models.py
from pathlib import Path
import yaml
from datetime import datetime
from typing import Optional # Optional をインポート

# common.py から JOB_STATUS 定数をインポート
from ..config.common import (
    JOB_STATUS_PENDING,
    JOB_STATUS_PREPROCESSING_STARTED,
    JOB_STATUS_CRF_SEARCH_STARTED,
    JOB_STATUS_PREPROCESSING_DONE,
    JOB_STATUS_ENCODING_FFMPEG_STARTED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_ERROR_RETRYABLE,
    JOB_STATUS_ERROR_PERMANENT,
    JOB_STATUS_SKIPPED
)

class EncodeInfo:
    """
    Manages information about encoding attempts for a file, stored in a YAML file.
    The YAML file is named after the hash of the original media file.
    Includes job status and retry logic.
    """
    def __init__(self, file_hash: str, encoder: str = "", crf: int = 0, storage_dir: Path = Path(".")):
        """
        Initialize the EncodeInfo instance.

        Args:
            file_hash (str): The hash (e.g., MD5) of the media file this info pertains to.
            encoder (str, optional): Default encoder if known. Defaults to "".
            crf (int, optional): Default CRF value if known. Defaults to 0.
            storage_dir (Path, optional): Directory where the .yaml info file will be stored. Defaults to current dir.
        """
        if not file_hash:
            raise ValueError("file_hash cannot be empty for EncodeInfo.")
        self.file_hash = file_hash
        self.encoder = encoder
        self.crf = crf
        self.storage_dir = storage_dir.resolve()
        self.storage_dir.mkdir(parents=True, exist_ok=True) # Ensure storage directory exists
        self.path = self.storage_dir / f"{self.file_hash}.progress.yaml" # Changed filename
        self.ori_video_path: Optional[str] = None # Path of the original video file

        # New attributes for status management
        self.status: str = JOB_STATUS_PENDING
        self.ffmpeg_command: Optional[str] = None
        self.temp_output_path: Optional[str] = None # ffmpegが実際に出力するファイルのパス
        self.last_error_message: Optional[str] = None
        self.retry_count: int = 0
        self.last_updated: Optional[str] = datetime.now().isoformat()
        self.pre_encoder_data: Optional[dict] = None # Store pre-encoder results if needed for resume

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
        Update attributes and save them to the YAML file.
        """
        if status: self.status = status
        if encoder: self.encoder = encoder # Only update if provided
        if crf is not None: self.crf = crf         # Update if crf is not None (0 is a valid CRF)
        if ori_video_path: self.ori_video_path = ori_video_path
        if ffmpeg_command: self.ffmpeg_command = ffmpeg_command
        if temp_output_path: self.temp_output_path = temp_output_path # Allow clearing by passing None
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
        Load attributes from the YAML file if it exists.

        Returns:
            bool: True if the file was successfully loaded, otherwise False.
        """
        if self.path.exists() and self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    obj_dict = yaml.safe_load(f)
                if obj_dict:
                    self.file_hash = obj_dict.get("file_hash", self.file_hash) # Should match
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
        Remove the YAML file if it exists.
        """
        try:
            if self.path.exists() and self.path.is_file():
                self.path.unlink()
        except OSError as e:
            print(f"Error removing EncodeInfo file {self.path}: {e}")