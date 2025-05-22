from pathlib import Path
import yaml

class EncodeInfo:
    """
    Manages information about encoding attempts for a file, stored in a YAML file.
    The YAML file is named after the hash of the original media file.
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
        self.path = self.storage_dir / f"{self.file_hash}.yaml"
        self.ori_video_path: str | None = None # Path of the original video file

    def dump(self, encoder: str = "", crf: int = 0, ori_video_path: str = ""):
        """
        Update the encoder, CRF, and original video path, then save them to the YAML file.

        Args:
            encoder (str, optional): The encoder used.
            crf (int, optional): The CRF value used.
            ori_video_path (str, optional): The path to the original video file.
        """
        self.encoder = encoder if encoder else self.encoder # Keep existing if new is empty
        self.crf = crf if crf else self.crf # Keep existing if new is 0/None
        self.ori_video_path = ori_video_path if ori_video_path else self.ori_video_path

        dump_dict = {
            "encoder": self.encoder,
            "crf": self.crf,
            "path": self.ori_video_path,
            "file_hash": self.file_hash # Good to store the hash itself in the file too
        }

        # Only write if there's meaningful data to save
        if self.encoder or self.crf or self.ori_video_path:
            try:
                with self.path.open("w", encoding="utf-8") as f:
                    yaml.dump(dump_dict, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            except Exception as e:
                # Consider using loguru here if it's available globally, or raise
                print(f"Error dumping EncodeInfo to {self.path}: {e}") # Or use logger

    def load(self) -> bool:
        """
        Load the encoder, CRF, and original video path from the YAML file if it exists.

        Returns:
            bool: True if the file was successfully loaded, otherwise False.
        """
        if self.path.exists() and self.path.is_file():
            try:
                with self.path.open("r", encoding="utf-8") as f:
                    obj_dict = yaml.safe_load(f)
                if obj_dict: # Check if YAML parsing returned a non-empty dict
                    self.encoder = obj_dict.get("encoder", "")
                    self.crf = obj_dict.get("crf", 0)
                    self.ori_video_path = obj_dict.get("path", "")
                    # Could also verify self.file_hash == obj_dict.get("file_hash")
                    return True
            except yaml.YAMLError as e:
                print(f"Error loading or parsing EncodeInfo from {self.path}: {e}") # Or use logger
                # Potentially delete or rename corrupted file
                # self.path.unlink(missing_ok=True)
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
            print(f"Error removing EncodeInfo file {self.path}: {e}") # Or use logger