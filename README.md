# Smart Encoder

Smart Encoder is a powerful tool for video and audio encoding that optimizes media quality while minimizing file size. It automatically selects encoding parameters based on content and desired output characteristics, with options for manual control.

## Features

- **Automatic Codec Selection**: Primarily uses libsvtav1 for video, with options for other codecs.
- **CRF Optimization (Video)**:
    - Finds an optimal Constant Rate Factor (CRF) using `ab-av1` for a good quality-to-size ratio, targeting a configurable VMAF score.
    - If `ab-av1` CRF search fails or is skipped (e.g., manual mode), it uses a `MANUAL_CRF` (default: 23, often considered visually lossless).
    - If the encoded file size exceeds the original, the CRF is incrementally increased (by `MANUAL_CRF_INCREMENT_PERCENT`, default: 15%), and encoding is re-attempted to achieve a smaller file size.
- **Audio Encoding**: Supports audio-only encoding and re-encoding of audio tracks within video files, including conversion to Opus.
- **iPhone Specific Presets**: Includes presets for encoding videos suitable for iPhone devices.
- **Multi-Process Encoding**: Supports encoding multiple files in parallel to speed up the process. The number of concurrent processes is configurable via a command-line argument.
- **Flexible Stream Handling**:
    - Selects video, audio, and subtitle streams based on configured language preferences and quality.
    - Option to encode video even if no suitable audio stream is found (`--allow-no-audio`).
- **File Management**:
    - Moves processed raw files to a separate archive directory (optional).
    - Standardizes filenames (optional).
    - Cleans up empty directories and temporary files.
- **Comprehensive Logging**: Detailed success and error logs are generated for easy troubleshooting and monitoring.
- **Customizable Configuration**: Advanced users can customize default settings through Python configuration files.

## Requirements

- Python 3.8+
- [FFmpeg](https://ffmpeg.org/download.html) installed and accessible (either in system PATH or specified module path).
- [ab-av1](https://github.com/alexheretic/ab-av1/releases/) installed and accessible in system PATH (required for automatic CRF optimization for video).
- `faster-whisper` and its dependencies (like PyTorch) for audio language detection (see `requirements.txt`).

## Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/kerasty2024/smart_encoder.git
    cd smart_encoder
    ```

2.  Create a virtual environment (recommended) and activate it:
    ```bash
    python -m venv venv
    # On Windows
    venv\Scripts\activate
    # On macOS/Linux
    source venv/bin/activate
    ```

3.  Install the required packages:
    ```bash
    pip install -r requirements.txt
    ```

4.  **Module Configuration (FFmpeg, etc.)**:
    *   By default, the script attempts to use FFmpeg and `ab-av1` from the system PATH.
    *   Alternatively, you can place these executables in a specific directory and configure their paths in `smart_encoder/config/common.py` (`MODULE_PATH`). The script also supports an auto-update mechanism from `MODULE_UPDATE_PATH` if configured.

## Usage

1.  **Prepare Media Files**: Place the video or audio files you want to encode in a source directory.
2.  **Run the Encoder**: Navigate to the project root directory (where `main.py` is located) in your terminal.

    **Standard Video Encoding Example:**
    ```bash
    # Encode videos in the current directory using 3 processes, move original files after success
    python -O main.py --processes 3 --move-raw-file
    ```

    **iPhone Specific Encoding Example:**
    ```bash
    # Encode for iPhone, assuming files are in the current directory
    python -O main.py --iphone-specific-task --processes 1
    ```

    **Audio-Only Encoding (e.g., for iPhone Audiobook preset):**
    ```bash
    # Process audio files (or extract/re-encode audio from videos) for iPhone
    python -O main.py --iphone-specific-task --audio-only --move-raw-file
    ```

    **Key Command-Line Options:**
    *   `--processes <N>`: Number of parallel processes to use (default: 4).
    *   `--move-raw-file`: Move the original source file to a `_raw` directory after successful encoding.
    *   `--not-rename`: Disable automatic standardization of filenames.
    *   `--manual-mode`: Use manually defined CRF and encoder settings (from config) instead of `ab-av1` search for video.
    *   `--allow-no-audio`: If a video file has no suitable audio stream, encode the video without audio instead of treating it as an error.
    *   `--log-level <LEVEL>`: Set logging verbosity (DEBUG, INFO, WARNING, ERROR). Default is INFO (DEBUG if `-O` Python flag is not used).
    *   `--target-dir <PATH>`: Specify a target directory to process instead of the current working directory.
    *   For a full list of options, run:
        ```bash
        python main.py --help
        ```

    **Debug Mode:**
    To see more detailed log messages (DEBUG level) in the console, run Python without the `-O` (optimize) flag:
    ```bash
    python main.py [options...]
    ```

3.  **Check Logs**:
    *   **Success Logs**: YAML files detailing successful encodings are typically stored in the respective output subdirectories (e.g., `libsvtav1_encoded/.../log_YYYYMMDD_random.yaml`). A combined log (`combined_log.yaml`) is generated in the root of the processed directory.
    *   **Error Logs**: Stored in `encode_error/<error_type_or_code>/.../error.txt` relative to the processed directory.
    *   **Command Logs**: The actual FFmpeg commands executed are logged to `cmd.txt` within the output directory of each file.

## Configuration

Default settings for encoding parameters, output directories, language preferences, and tool paths can be customized by editing the Python files in the `smart_encoder/config/` directory:
*   `smart_encoder/config/common.py`: General paths, logging, language codes.
*   `smart_encoder/config/video.py`: Video encoding defaults (CRF, VMAF, encoders, iPhone presets, etc.).
*   `smart_encoder/config/audio.py`: Audio encoding defaults (target bitrates, etc.).

Adjust these files to suit your needs before running the encoder.

## Contributing

Contributions are welcome! If you have ideas for improvements, new features, or bug fixes, please feel free to open an issue or submit a pull request.

## Support via Sponsorship Platforms
- **GitHub Sponsors**: [https://github.com/sponsors/kerasty2024](https://github.com/sponsors/kerasty2024)
- **Buy Me a Coffee**: [https://buymeacoffee.com/kerasty](https://buymeacoffee.com/kerasty)

## Support with Cryptocurrency
Support the project by sending cryptocurrency to the following addresses. Every contribution helps fund development, server costs, and future enhancements!

| Cryptocurrency | Address                                                   | QR Code                                          |
|----------------|-----------------------------------------------------------|--------------------------------------------------|
| Bitcoin (BTC)  | bc1qn72yvftnuh7jgjnn9x848pzhhywasxmqt5c7wp                  | ![BTC QR Code](contents/crypto/BTC_QR.jpg)       |
| Ethereum (ETH) | 0x2175Ed9c75C14F113ab9cEaDc1890b2f87f40e78                  | ![ETH QR Code](contents/crypto/ETH_QR.jpg)       |
| Solana (SOL)   | 6Hc7erZqgreTVwCsTtNvsyzigN2oHJ4EgNGaLWtRWJ69                  | ![Solana QR Code](contents/crypto/Solana_QR.jpg) |

**How to Contribute with Crypto**:
1. Copy the address or scan the QR code using your wallet app.
2. Send any amount to support the project.
3. Your contribution helps keep this project alive—thank you!

## License
See [LICENSE](LICENSE) for details(MIT License).