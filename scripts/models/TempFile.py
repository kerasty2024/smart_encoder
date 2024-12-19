from pathlib import Path

import yaml


class EncodeInfo:
    def __init__(self, file_hash: str, encoder: str = "", crf: int = 0):
        """
        Initialize the EncodeInfo instance with the given file hash, encoder, and CRF.
        The file path for storing the information is derived from the hash.
        """
        self.file_hash = file_hash
        self.encoder = encoder
        self.crf = crf
        self.path = Path(f"{file_hash}.yaml")
        self.ori_video_path = None

    def dump(self, encoder: str = "", crf: int = 0, ori_video_path: str = ""):
        """
        Update the encoder, CRF, and original video path, and save them to the YAML file.
        """
        self.encoder = encoder
        self.crf = crf
        self.ori_video_path = ori_video_path

        dump_dict = {
            "encoder": self.encoder,
            "crf": self.crf,
            "path": self.ori_video_path,
        }

        if self.encoder or self.crf:
            with self.path.open("w", encoding="utf-8") as f:
                yaml.dump(dump_dict, f, default_flow_style=False, sort_keys=False)

    def load(self) -> bool:
        """
        Load the encoder and CRF from the YAML file if it exists.
        Return True if the file was successfully loaded, otherwise False.
        """
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                obj_dict = yaml.safe_load(f)
            self.encoder = obj_dict.get("encoder", "")
            self.crf = obj_dict.get("crf", 0)
            self.ori_video_path = obj_dict.get("path", "")
            return True
        return False

    def remove_file(self):
        """
        Remove the YAML file if it exists.
        """
        if self.path.exists():
            self.path.unlink()
