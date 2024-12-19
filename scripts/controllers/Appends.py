import os
import shutil
import subprocess
from pathlib import Path

from loguru import logger

from scripts.settings.common import MODULE_PATH, MODULE_UPDATE_PATH


class Modules:
    """
    A class to handle operations related to module updates for a specific application.

    This class provides a static method to update modules by moving files from an update
    directory to the main module directory and then verifying the installation of FFmpeg.
    """

    @staticmethod
    def update():
        """
        Update the modules by moving files from the update directory to the module directory
        and check the installed FFmpeg version.

        This method performs the following steps:
        1. Move all files from MODULE_UPDATE_PATH to MODULE_PATH.
        2. Log the success or failure of each file move operation.
        3. Run the 'ffmpeg -version' command to verify that FFmpeg is installed and log its version.

        If the FFmpeg command is not found or fails, log an error message.
        """
        module_update_path = Path(MODULE_UPDATE_PATH)
        module_path = Path(MODULE_PATH)

        for update_file in module_update_path.glob("*"):
            try:
                destination = module_path / update_file.name
                shutil.move(update_file, destination)
                logger.info(f"Successfully moved {update_file} to {destination}")
            except Exception as e:
                logger.error(f"Failed to move {update_file}: {e}")

        try:
            result = subprocess.run(
                ["ffmpeg", "-version"], check=True, capture_output=True, text=True
            )
            logger.info(f"FFmpeg version:{os.linesep}{result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to get FFmpeg version: {e}")
        except FileNotFoundError:
            logger.error(
                "FFmpeg command not found. Please ensure FFmpeg is installed and in your PATH."
            )
