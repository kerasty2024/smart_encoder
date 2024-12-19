# Smart Encoder

Smart Encoder is a powerful tool for video encoding that optimizes video quality while minimizing file size. It
automatically selects the best encoding parameters based on video content and desired output characteristics.

## Features

- **Automatic Codec Selection**: Automatically selects the best video codec for encoding (default: libsvtav1).
- **CRF Optimization**: Finds the optimal Constant Rate Factor (CRF) for the best quality-to-size ratio by ab-av1. If
  suitable crf cannot be defined, use MANUAL_CRF(default: 23, usually recognized as visually lossless). If encoded file
  size > original file size, increment CRF by MANUAL_CRF_INCREMENT_PERCENT (default: 15%) and encode again, until
  encoded file size is smaller than original one.
- **Multi-Process Encoding**: Supports encoding multiple files in parallel to speed up the process. # of processors can
  be configured by command-line.
- **Comprehensive Logging**: Logs success and error details for easy troubleshooting and monitoring.
- **Flexible Configuration**: Customizable settings for advanced users through configuration files.

## Requirements

- Python 3.7+
- [FFmpeg](https://ffmpeg.org/download.html) installed and added to PATH
- [ab-av1](https://github.com/alexheretic/ab-av1/releases/) added to PATH for searching best codec and CRF.

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/kerasty2024/smart_encoder.git
   cd smart_encoder

2. Create a virtual environment and activate it:

    ```bash
    python -m venv venv
    venv/bin/activate  # On Windows use `venv\Scripts\activate`

3. Install the required packages:

    ```bash
    pip install -r requirements.txt

4. Ensure FFmpeg & ab-av1 and so on (refer to Requirements) is installed and accessible from your command line.

## Usage

1. Prepare your media files: Place the media files you want to encode in a source directory.
2. Run the encoder:

    ```bash
    python -O main.py --processes 3 --move-raw-file  # you can also modify # of processes, depending on how powerful your environmet is.

- Use --manual-mode if you wish to manually set encoding parameters. manual crf can be set in settings/video.py

    ```bash
    python -O main.py --processes 1 --move-raw-file --manual-mode # you can also modify # of processes, depending on how powerful your environmet is.
- If you want more message shown in console, remove -O option (change to debug mode).
- other options can be found in main.py

3. Check logs:

- Success logs are stored in the output directory with details of encoded files.
- Error logs are available in the same directory for any issues encountered during encoding.

## Configuration

.py files in settings directory allow you to adjust default settings for encoding parameters and language detection
preferences. Customize this file to suit your needs before running the encoder.

## Contributing

Contributions are welcome! If you have ideas for improvements or new features, feel free to open an issue or submit a
pull request.

## License

This project is licensed under the MIT License. 