"""
Utilities Package for the Smart Encoder Application.

This package contains various helper modules and utility functions that provide
common, reusable functionality across the entire application. These utilities
are not specific to any single part of the encoding domain but support various
tasks such as running external commands, formatting data for display, or handling
module updates.

Modules:
    - ffmpeg_utils.py: Provides functions for interacting with external tools
      like FFmpeg and for performing complex tasks like language detection.
    - format_utils.py: Contains helper functions for formatting data, such as
      converting timedelta objects or file sizes into human-readable strings.
    - module_updater.py: Manages the verification and updating of external
      dependencies like FFmpeg.
"""