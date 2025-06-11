"""
Provides the Modules class to handle updates and verification of external tools
like FFmpeg.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from loguru import logger

from ..config.common import MODULE_PATH, MODULE_UPDATE_PATH


class Modules:
    """
    A class to handle operations related to FFmpeg module updates and verification.
    It reads paths from the user configuration and provides a fallback to the
    system's PATH.
    """

    @staticmethod
    def update():
        """
        Updates modules from the `module_update_dir` specified in the user config.

        If the update path is configured and exists, this method moves all its
        contents to the main module directory (`ffmpeg_dir`), overwriting
        existing files if necessary. If the path is not configured, this step
        is silently skipped.
        """
        if not MODULE_UPDATE_PATH:
            logger.debug(
                "`module_update_dir` not configured. Skipping module update check."
            )
            return

        if not MODULE_PATH:
            logger.error(
                f"Module update path '{MODULE_UPDATE_PATH}' is set, but the destination "
                f"`ffmpeg_dir` is not. Cannot perform update."
            )
            return

        if not MODULE_UPDATE_PATH.is_dir():
            logger.warning(
                f"Configured module update directory '{MODULE_UPDATE_PATH}' does not exist or is not a directory. Skipping update."
            )
            return

        logger.info(
            f"Checking for module updates from '{MODULE_UPDATE_PATH}' to '{MODULE_PATH}'"
        )

        update_files_found = list(MODULE_UPDATE_PATH.glob("*"))
        if not update_files_found:
            logger.info("No files found in module update directory.")
        else:
            MODULE_PATH.mkdir(parents=True, exist_ok=True)
            for update_item_path in update_files_found:
                destination_path = MODULE_PATH / update_item_path.name
                try:
                    if destination_path.is_dir() and update_item_path.is_dir():
                        logger.info(
                            f"Destination '{destination_path.name}' is a directory. Removing it before moving new one."
                        )
                        shutil.rmtree(destination_path)

                    shutil.move(str(update_item_path), str(destination_path))
                    logger.info(
                        f"Successfully moved '{update_item_path.name}' to '{destination_path}'"
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to move '{update_item_path.name}' to '{destination_path}': {e}"
                    )

    @staticmethod
    def _get_ffmpeg_path() -> str:
        """
        Determines the correct FFmpeg executable to use.

        It prioritizes the path from the user configuration (`ffmpeg_dir`).
        If not set, it falls back to 'ffmpeg', relying on the system's PATH.
        It handles platform-specific executable names (e.g., '.exe' on Windows).

        Returns:
            The command or path to the FFmpeg executable.
        """
        # Determine the executable name based on the OS
        ffmpeg_exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

        if MODULE_PATH and MODULE_PATH.is_dir():
            configured_ffmpeg_path = MODULE_PATH / ffmpeg_exe_name
            if configured_ffmpeg_path.is_file():
                logger.debug(
                    f"Using FFmpeg from configured path: '{configured_ffmpeg_path}'"
                )
                return str(configured_ffmpeg_path)
            else:
                logger.warning(
                    f"`ffmpeg_dir` is configured to '{MODULE_PATH}', but '{ffmpeg_exe_name}' was not found there. "
                    "Falling back to system PATH."
                )

        # Fallback to system PATH
        return "ffmpeg"

    @staticmethod
    def verify_ffmpeg():
        """
        Verifies that FFmpeg is installed and accessible.

        This method runs `ffmpeg -version` and logs the output. It will log an
        error if the command cannot be found or fails to execute.
        """
        ffmpeg_cmd = Modules._get_ffmpeg_path()

        try:
            result = subprocess.run(
                [ffmpeg_cmd, "-version"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            version_output_lines = result.stdout.splitlines()
            logger.info(
                f"FFmpeg version check successful. Output (first line):\n"
                f"{version_output_lines[0]}"
            )
        except subprocess.CalledProcessError as e:
            logger.error(
                f"FFmpeg version command failed (return code {e.returncode}):\n{e.stderr}"
            )
        except FileNotFoundError:
            logger.error(
                "FFmpeg command not found. Please ensure FFmpeg is installed and accessible.\n"
                "You can either add it to your system's PATH or specify its location "
                "in the 'config.user.yaml' file."
            )
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while checking FFmpeg version: {e}"
            )

    @staticmethod
    def run_all():
        """
        A convenience method to run all checks and updates in sequence.
        """
        Modules.update()
        Modules.verify_ffmpeg()