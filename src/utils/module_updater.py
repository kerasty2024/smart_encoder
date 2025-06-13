"""
This module provides the Modules class to handle the verification and updating
of external tools required by the application, such as FFmpeg.
"""
import shutil
import subprocess
import sys

from loguru import logger

# Import paths from the user configuration file.
from ..config.common import MODULE_PATH, MODULE_UPDATE_PATH


class Modules:
    """
    A utility class to handle operations related to external modules like FFmpeg.

    It reads paths from the user's `config.user.yaml` file to locate necessary
    executables and to find updates for them. It provides a fallback to the
    system's PATH if no specific path is configured.
    """

    @staticmethod
    def update():
        """
        Updates external modules from the `module_update_dir` specified in the user config.

        If an update directory is configured and exists, this method moves all its
        contents to the main module directory (`ffmpeg_dir`), overwriting existing
        files if necessary. This provides a simple mechanism for users to drop in
        new versions of tools like FFmpeg.

        If the update path is not configured, this step is silently skipped.
        """
        if not MODULE_UPDATE_PATH:
            logger.debug("`module_update_dir` not configured in user config. Skipping module update check.")
            return

        if not MODULE_PATH:
            logger.error(f"Cannot perform update: The update path '{MODULE_UPDATE_PATH}' is set, but the destination `ffmpeg_dir` is not.")
            return

        if not MODULE_UPDATE_PATH.is_dir():
            logger.warning(f"Configured module update directory '{MODULE_UPDATE_PATH}' does not exist. Skipping update.")
            return

        logger.info(f"Checking for module updates from '{MODULE_UPDATE_PATH}' to '{MODULE_PATH}'...")

        update_files_found = list(MODULE_UPDATE_PATH.glob("*"))
        if not update_files_found:
            logger.info("No files found in module update directory. Nothing to do.")
        else:
            MODULE_PATH.mkdir(parents=True, exist_ok=True)
            for update_item_path in update_files_found:
                destination_path = MODULE_PATH / update_item_path.name
                try:
                    # If the destination is a directory, remove it before moving the new one to prevent merging.
                    if destination_path.is_dir() and update_item_path.is_dir():
                        logger.info(f"Removing existing directory '{destination_path.name}' before update.")
                        shutil.rmtree(destination_path)

                    # Move the new file or directory to the target module path.
                    shutil.move(str(update_item_path), str(destination_path))
                    logger.info(f"Successfully moved '{update_item_path.name}' to '{destination_path}'")
                except Exception as e:
                    logger.error(f"Failed to move '{update_item_path.name}' to '{destination_path}': {e}")

    @staticmethod
    def _get_ffmpeg_path() -> str:
        """
        Determines the correct FFmpeg executable path to use.

        It prioritizes the path from the user configuration (`ffmpeg_dir`).
        If that is not set or invalid, it falls back to 'ffmpeg', which relies on the
        executable being available in the system's PATH. It also handles
        platform-specific executable names (e.g., adding '.exe' on Windows).

        Returns:
            A string containing the command or absolute path to the FFmpeg executable.
        """
        # Determine the executable name based on the operating system.
        ffmpeg_exe_name = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"

        # Prioritize the path from the user config.
        if MODULE_PATH and MODULE_PATH.is_dir():
            configured_ffmpeg_path = MODULE_PATH / ffmpeg_exe_name
            if configured_ffmpeg_path.is_file():
                logger.debug(f"Using FFmpeg from configured path: '{configured_ffmpeg_path}'")
                return str(configured_ffmpeg_path)
            else:
                logger.warning(f"`ffmpeg_dir` is configured, but '{ffmpeg_exe_name}' was not found there. Falling back to system PATH.")

        # If no configured path, fall back to assuming 'ffmpeg' is in the system PATH.
        return "ffmpeg"

    @staticmethod
    def verify_ffmpeg():
        """
        Verifies that FFmpeg is installed, accessible, and can be executed.

        This method runs `ffmpeg -version`, logs the first line of the output on
        success, and logs a detailed error message if the command fails or if
        FFmpeg cannot be found. This is a crucial check at application startup.
        """
        ffmpeg_cmd = Modules._get_ffmpeg_path()

        try:
            # Run the 'ffmpeg -version' command.
            result = subprocess.run(
                [ffmpeg_cmd, "-version"],
                check=True,          # Raise an exception if the command returns a non-zero exit code.
                capture_output=True, # Capture stdout and stderr.
                text=True,
                encoding="utf-8",
            )
            # Log the first line of the version output for confirmation.
            version_output_lines = result.stdout.splitlines()
            logger.info(f"FFmpeg version check successful. Output (first line):\n{version_output_lines[0]}")
        except subprocess.CalledProcessError as e:
            logger.error(f"FFmpeg version command failed (return code {e.returncode}):\n{e.stderr}")
        except FileNotFoundError:
            logger.error(
                "FFmpeg command not found. Please ensure FFmpeg is installed and accessible.\n"
                "You can either add it to your system's PATH or specify its location in the 'config.user.yaml' file."
            )
        except Exception as e:
            logger.error(f"An unexpected error occurred while checking FFmpeg version: {e}")

    @staticmethod
    def run_all():
        """
        A convenience method to run all startup checks and updates in sequence.
        This is typically called once when the application starts.
        """
        Modules.update()
        Modules.verify_ffmpeg()