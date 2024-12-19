import concurrent.futures
import os
from pathlib import Path

from loguru import logger

from scripts.models.Encoder import Encoder, PhoneVideoEncoder, AudioEncoder
from scripts.models.Log import SuccessLog
from scripts.models.MediaFile import MediaFile
from scripts.models.ProcessFiles import ProcessFiles, ProcessPhoneFiles, ProcessAudioFiles
from scripts.settings.audio import TARGET_BIT_RATE_IPHONE_XR, AUDIO_ENCODED_ROOT_DIR
from scripts.settings.video import OUTPUT_DIR_IPHONE


class EncodeStarter:
    encoder: Encoder
    process_files: ProcessFiles
    encoded_dir: str

    def __init__(self, path=Path(os.getcwd()), args=None):
        self.project_dir: Path = path.absolute()
        self.args = args

    def process_single_file(self, path: str):
        pass


class PhoneEncodeStarter(EncodeStarter):

    def __init__(self, path=Path(os.getcwd()), args=None):
        super().__init__(path, args)
        self.encoded_dir = OUTPUT_DIR_IPHONE
        self.args = args

    def process_single_file(self, path: Path):
        media_file = MediaFile(path)
        if self.args.audio_only:
            encoder = AudioEncoder(media_file, target_bit_rate=TARGET_BIT_RATE_IPHONE_XR, args=self.args)
        else:
            encoder = PhoneVideoEncoder(media_file, args=self.args)
        encoder.start()

    def process_multi_file(self):
        if self.args.audio_only:
            self.process_files = ProcessAudioFiles(self.project_dir, self.args)
        else:
            self.process_files = ProcessPhoneFiles(self.project_dir, self.args)
        if not self.process_files.source_dir:
            return
        logger.info(f'remain files: {len(self.process_files.files)}')
        with concurrent.futures.ProcessPoolExecutor(max_workers=self.args.processes) as executor:
            for file in self.process_files.files:
                executor.submit(self.process_single_file, file)

    def post_actions(self):
        self.process_files.remove_empty_dirs()
        self.process_files.move_raw_folder_if_no_process_files(os.path.abspath(AUDIO_ENCODED_ROOT_DIR))
        SuccessLog.generate_combined_log_yaml(Path(self.project_dir))
