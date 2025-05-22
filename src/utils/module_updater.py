import os
import shutil
import subprocess
from pathlib import Path

from loguru import logger

# Config
from ..config.common import MODULE_PATH, MODULE_UPDATE_PATH


class Modules:  # Consider renaming to ModuleUpdater for clarity
    """
    A class to handle operations related to FFmpeg module updates and verification.
    It assumes FFmpeg and other tools are located in MODULE_PATH and updates
    are copied from MODULE_UPDATE_PATH.
    """

    @staticmethod
    def update():
        """
        Updates modules by moving files from an update directory to the main module
        directory and then verifies the installation of FFmpeg by checking its version.

        This method performs the following steps:
        1. Checks if MODULE_UPDATE_PATH and MODULE_PATH exist.
        2. If MODULE_UPDATE_PATH exists, moves all its contents to MODULE_PATH,
           overwriting existing files if names collide.
        3. Logs success or failure of each file move operation.
        4. Runs 'ffmpeg -version' command to verify FFmpeg is installed (expected in MODULE_PATH or system PATH)
           and logs its version.
        5. Logs an error if FFmpeg command is not found or fails.
        """
        # Ensure paths from config are Path objects and resolved
        module_update_dir = Path(MODULE_UPDATE_PATH).resolve()
        main_module_dir = Path(MODULE_PATH).resolve()

        # Step 1: Check existence of module directories
        if not main_module_dir.is_dir():
            logger.warning(
                f"Main module directory {main_module_dir} does not exist. Skipping module update checks."
            )
            # Cannot check FFmpeg if its expected location doesn't exist (unless it's in system PATH)
            # Proceed to FFmpeg check using system PATH.

        elif module_update_dir.is_dir():  # Only proceed with move if update dir exists
            logger.info(
                f"Checking for module updates from {module_update_dir} to {main_module_dir}"
            )

            update_files_found = list(module_update_dir.glob("*"))
            if not update_files_found:
                logger.info("No files found in module update directory.")
            else:
                main_module_dir.mkdir(
                    parents=True, exist_ok=True
                )  # Ensure main module dir exists for moving
                for update_item_path in update_files_found:
                    destination_path = main_module_dir / update_item_path.name
                    try:
                        # shutil.move can move files or directories.
                        # If destination_path exists and is a file, it's overwritten.
                        # If destination_path exists and is a dir, shutil.move might error if update_item_path is also a dir,
                        # unless the destination dir is empty or specific OS/Python version behavior.
                        # For simplicity, assuming direct move/overwrite of files.
                        # If update_item_path is a directory, this will move the directory *into* main_module_dir.
                        if (
                            destination_path.is_dir() and update_item_path.is_dir()
                        ):  # Overwriting a dir with another
                            logger.info(
                                f"Destination {destination_path.name} is a directory. Removing it before moving new one."
                            )
                            shutil.rmtree(destination_path)

                        shutil.move(str(update_item_path), str(destination_path))
                        logger.info(
                            f"Successfully moved {update_item_path.name} to {destination_path}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Failed to move {update_item_path.name} to {destination_path}: {e}"
                        )
        else:
            logger.info(
                f"Module update directory {module_update_dir} not found. No updates applied from there."
            )

        # Step 2: Verify FFmpeg installation
        # FFmpeg command might be in MODULE_PATH or system PATH.
        # Construct command to prefer ffmpeg from MODULE_PATH if it exists.
        ffmpeg_exe_path = (
            main_module_dir / "ffmpeg.exe"
        )  # Assuming Windows, adjust for cross-platform
        if not ffmpeg_exe_path.is_file():  # Fallback to system PATH ffmpeg
            ffmpeg_cmd = "ffmpeg"
            logger.debug(
                "ffmpeg.exe not found in main module directory. Trying system PATH for ffmpeg."
            )
        else:
            ffmpeg_cmd = str(ffmpeg_exe_path)
            logger.debug(f"Using ffmpeg from: {ffmpeg_cmd}")

        try:
            # Use subprocess.run for better control and output capture.
            # `check=True` will raise CalledProcessError if ffmpeg returns non-zero.
            # `text=True` decodes output as string.
            result = subprocess.run(
                [ffmpeg_cmd, "-version"],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",  # Explicit encoding
            )
            # Log first few lines of version output for brevity
            version_output_lines = result.stdout.splitlines()
            logger.info(
                f"FFmpeg version check successful. Output (first few lines):\n"
                f"{os.linesep.join(version_output_lines[:3])}"
            )  # Log first 3 lines
        except subprocess.CalledProcessError as e:
            logger.error(
                f"FFmpeg version command failed (return code {e.returncode}):\n{e.stderr}"
            )
        except FileNotFoundError:
            logger.error(
                "FFmpeg command not found. Please ensure FFmpeg is installed, "
                f"either in {main_module_dir} or in your system PATH."
            )
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while checking FFmpeg version: {e}"
            )