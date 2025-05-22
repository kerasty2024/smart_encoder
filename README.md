# Smart Encoder

[English](#smart-encoder-english) | [日本語](#smart-encoder-日本語版) | [简体中文](#smart-encoder-简体中文版)

---

## Smart Encoder (English)

Smart Encoder is a powerful tool for video and audio encoding that optimizes media quality while minimizing file size. It automatically selects encoding parameters based on content and desired output characteristics, with options for manual control.

### Features

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

### Requirements

- Python 3.8+
- [FFmpeg](https://ffmpeg.org/download.html) installed and accessible (either in system PATH or specified module path).
- [ab-av1](https://github.com/alexheretic/ab-av1/releases/) installed and accessible in system PATH (required for automatic CRF optimization for video).
- `faster-whisper` and its dependencies (like PyTorch) for audio language detection (see `requirements.txt`).

### Installation

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

### Usage

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

### Configuration

Default settings for encoding parameters, output directories, language preferences, and tool paths can be customized by editing the Python files in the `smart_encoder/config/` directory:
*   `smart_encoder/config/common.py`: General paths, logging, language codes.
*   `smart_encoder/config/video.py`: Video encoding defaults (CRF, VMAF, encoders, iPhone presets, etc.).
*   `smart_encoder/config/audio.py`: Audio encoding defaults (target bitrates, etc.).

Adjust these files to suit your needs before running the encoder.

### Contributing

Contributions are welcome! If you have ideas for improvements, new features, or bug fixes, please feel free to open an issue or submit a pull request.

### Support via Sponsorship Platforms
- **GitHub Sponsors**: [https://github.com/sponsors/kerasty2024](https://github.com/sponsors/kerasty2024)
- **Buy Me a Coffee**: [https://buymeacoffee.com/kerasty](https://buymeacoffee.com/kerasty)

### Support with Cryptocurrency
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

### License
This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

---

## Smart Encoder (日本語版)

Smart Encoderは、ファイルサイズを最小限に抑えながらメディア品質を最適化する、強力なビデオおよびオーディオエンコードツールです。コンテンツや目的の出力特性に基づいてエンコードパラメータを自動的に選択し、手動制御のオプションも提供します。

### 特徴

- **自動コーデック選択**: 主にビデオにはlibsvtav1を使用し、他のコーデックのオプションも利用可能です。
- **CRF最適化 (ビデオ)**:
    - 設定可能なVMAFスコアをターゲットとし、品質とサイズの比率が良好な最適な固定レート係数（CRF）を`ab-av1`を使用して見つけ出します。
    - `ab-av1`によるCRF検索が失敗した場合やスキップされた場合（例：マニュアルモード）、`MANUAL_CRF`（デフォルト：23、多くの場合視覚的にロスレスと見なされる）を使用します。
    - エンコードされたファイルサイズが元のファイルサイズを超えた場合、CRFを段階的に増加させ（`MANUAL_CRF_INCREMENT_PERCENT`、デフォルト：15%）、より小さなファイルサイズを達成するためにエンコードを再試行します。
- **オーディオエンコード**: オーディオのみのエンコードや、ビデオファイル内のオーディオトラックの再エンコード（Opusへの変換を含む）をサポートします。
- **iPhone特有のプリセット**: iPhoneデバイスに適したビデオをエンコードするためのプリセットが含まれています。
- **マルチプロセスエンコード**: 処理を高速化するために、複数のファイルを並行してエンコードすることをサポートします。同時実行プロセス数はコマンドライン引数で設定可能です。
- **柔軟なストリーム処理**:
    - 設定された言語設定と品質に基づいて、ビデオ、オーディオ、字幕ストリームを選択します。
    - 適切なオーディオストリームが見つからない場合でもビデオをエンコードするオプション（`--allow-no-audio`）。
- **ファイル管理**:
    - 処理済みのRAWファイルを別のアーカイブディレクトリに移動します（オプション）。
    - ファイル名を標準化します（オプション）。
    - 空のディレクトリや一時ファイルをクリーンアップします。
- **包括的なロギング**: トラブルシューティングや監視を容易にするために、成功とエラーの詳細なログが生成されます。
- **カスタマイズ可能な設定**: 上級ユーザーは、Python設定ファイルを介してデフォルト設定をカスタマイズできます。

### 要件

- Python 3.8以降
- [FFmpeg](https://ffmpeg.org/download.html) がインストールされ、アクセス可能であること（システムPATHまたは指定されたモジュールパスのいずれか）。
- [ab-av1](https://github.com/alexheretic/ab-av1/releases/) がインストールされ、システムPATHでアクセス可能であること（ビデオの自動CRF最適化に必要）。
- オーディオ言語検出のための`faster-whisper`とその依存関係（PyTorchなど）（`requirements.txt`を参照）。

### インストール

1.  リポジトリをクローンします:
    ```bash
    git clone https://github.com/kerasty2024/smart_encoder.git
    cd smart_encoder
    ```

2.  仮想環境を作成（推奨）し、アクティベートします:
    ```bash
    python -m venv venv
    # Windowsの場合
    venv\Scripts\activate
    # macOS/Linuxの場合
    source venv/bin/activate
    ```

3.  必要なパッケージをインストールします:
    ```bash
    pip install -r requirements.txt
    ```

4.  **モジュール設定 (FFmpegなど)**:
    *   デフォルトでは、スクリプトはシステムPATHからFFmpegと`ab-av1`を使用しようとします。
    *   あるいは、これらの実行可能ファイルを特定のディレクトリに配置し、`smart_encoder/config/common.py`（`MODULE_PATH`）でパスを設定することもできます。スクリプトは、設定されていれば`MODULE_UPDATE_PATH`からの自動更新メカニズムもサポートしています。

### 使用方法

1.  **メディアファイルの準備**: エンコードしたいビデオまたはオーディオファイルをソースディレクトリに配置します。
2.  **エンコーダーの実行**: ターミナルでプロジェクトのルートディレクトリ（`main.py`がある場所）に移動します。

    **標準ビデオエンコードの例:**
    ```bash
    # 現在のディレクトリのビデオを3プロセス使用してエンコードし、成功後に元のファイルを移動
    python -O main.py --processes 3 --move-raw-file
    ```

    **iPhone特有のエンコードの例:**
    ```bash
    # iPhone用にエンコード（ファイルが現在のディレクトリにあると仮定）
    python -O main.py --iphone-specific-task --processes 1
    ```

    **オーディオのみのエンコードの例 (例：iPhoneオーディオブックプリセット):**
    ```bash
    # iPhone用にオーディオファイルを処理（またはビデオからオーディオを抽出/再エンコード）
    python -O main.py --iphone-specific-task --audio-only --move-raw-file
    ```

    **主要なコマンドラインオプション:**
    *   `--processes <N>`: 使用する並列プロセス数（デフォルト：4）。
    *   `--move-raw-file`: エンコード成功後、元のソースファイルを`_raw`ディレクトリに移動します。
    *   `--not-rename`: ファイル名の自動標準化を無効にします。
    *   `--manual-mode`: ビデオに対して`ab-av1`検索の代わりに、手動で定義されたCRFとエンコーダー設定（設定ファイルから）を使用します。
    *   `--allow-no-audio`: ビデオファイルに適切なオーディオストリームがない場合、エラーとして扱わずにオーディオなしでビデオをエンコードします。
    *   `--log-level <LEVEL>`: ロギングの詳細度を設定します（DEBUG, INFO, WARNING, ERROR）。デフォルトはINFO（Pythonの`-O`フラグが使用されていない場合はDEBUG）。
    *   `--target-dir <PATH>`: 現在の作業ディレクトリの代わりに処理するターゲットディレクトリを指定します。
    *   オプションの完全なリストについては、以下を実行してください:
        ```bash
        python main.py --help
        ```

    **デバッグモード:**
    コンソールでより詳細なログメッセージ（DEBUGレベル）を表示するには、Pythonを`-O`（最適化）フラグなしで実行します:
    ```bash
    python main.py [options...]
    ```

3.  **ログの確認**:
    *   **成功ログ**: エンコード成功の詳細を示すYAMLファイルは、通常、それぞれの出力サブディレクトリ（例：`libsvtav1_encoded/.../log_YYYYMMDD_random.yaml`）に保存されます。結合されたログ（`combined_log.yaml`）は、処理されたディレクトリのルートに生成されます。
    *   **エラーログ**: 処理されたディレクトリに対して相対的な`encode_error/<error_type_or_code>/.../error.txt`に保存されます。
    *   **コマンドログ**: 実行された実際のFFmpegコマンドは、各ファイルの出力ディレクトリ内の`cmd.txt`に記録されます。

### 設定

エンコードパラメータ、出力ディレクトリ、言語設定、ツールパスのデフォルト設定は、`smart_encoder/config/`ディレクトリ内のPythonファイルを編集することでカスタマイズできます:
*   `smart_encoder/config/common.py`: 一般的なパス、ロギング、言語コード。
*   `smart_encoder/config/video.py`: ビデオエンコードのデフォルト（CRF, VMAF, エンコーダー, iPhoneプリセットなど）。
*   `smart_encoder/config/audio.py`: オーディオエンコードのデフォルト（ターゲットビットレートなど）。

エンコーダーを実行する前に、これらのファイルを必要に応じて調整してください。

### 貢献

貢献を歓迎します！改善や新機能のアイデアがある場合は、遠慮なくissueを開くか、プルリクエストを送信してください。

### スポンサーシッププラットフォーム経由でのサポート
- **GitHub Sponsors**: [https://github.com/sponsors/kerasty2024](https://github.com/sponsors/kerasty2024)
- **Buy Me a Coffee**: [https://buymeacoffee.com/kerasty](https://buymeacoffee.com/kerasty)

### 暗号通貨によるサポート
以下のアドレスに暗号通貨を送ることで、プロジェクトをサポートしてください。すべての貢献は、開発、サーバー費用、将来の機能強化の資金となります！

| 暗号通貨     | アドレス                                                   | QRコード                                          |
|----------------|-----------------------------------------------------------|--------------------------------------------------|
| ビットコイン (BTC)  | bc1qn72yvftnuh7jgjnn9x848pzhhywasxmqt5c7wp                  | ![BTC QRコード](contents/crypto/BTC_QR.jpg)       |
| イーサリアム (ETH) | 0x2175Ed9c75C14F113ab9cEaDc1890b2f87f40e78                  | ![ETH QRコード](contents/crypto/ETH_QR.jpg)       |
| Solana (SOL)   | 6Hc7erZqgreTVwCsTtNvsyzigN2oHJ4EgNGaLWtRWJ69                  | ![Solana QRコード](contents/crypto/Solana_QR.jpg) |

**暗号通貨で貢献する方法**:
1. ウォレットアプリを使用してアドレスをコピーするか、QRコードをスキャンします。
2. プロジェクトをサポートするために任意の金額を送金します。
3. あなたの貢献がこのプロジェクトを存続させます—ありがとうございます！

### ライセンス
このプロジェクトはMITライセンスの下でライセンスされています。詳細は[LICENSE](LICENSE)ファイルを参照してください。

---

## Smart Encoder (简体中文版)

Smart Encoder 是一款功能强大的视频和音频编码工具，可在最大限度减小文件大小的同时优化媒体质量。它会根据内容和期望的输出特性自动选择编码参数，并提供手动控制选项。

### 功能特性

- **自动编解码器选择**: 主要使用 libsvtav1 进行视频编码，并提供其他编解码器的选项。
- **CRF 优化 (视频)**:
    - 使用 `ab-av1` 针对可配置的 VMAF 分数，找到最佳的固定码率因子 (CRF)，以获得良好的质量与大小比率。
    - 如果 `ab-av1` CRF 搜索失败或被跳过 (例如，在手动模式下)，则使用 `MANUAL_CRF` (默认值：23，通常被认为是视觉无损的)。
    - 如果编码后的文件大小超过原始文件大小，则会逐步增加 CRF (通过 `MANUAL_CRF_INCREMENT_PERCENT`，默认值：15%)，并重新尝试编码以获得更小的文件大小。
- **音频编码**: 支持纯音频编码以及视频文件中音轨的重新编码 (包括转换为 Opus 格式)。
- **iPhone 特定预设**: 包含适用于 iPhone 设备的视频编码预设。
- **多进程编码**: 支持并行编码多个文件以加快处理速度。并发进程的数量可通过命令行参数配置。
- **灵活的流处理**:
    - 根据配置的语言偏好和质量选择视频、音频和字幕流。
    - 即使找不到合适的音频流，也可以选择编码视频 (通过 `--allow-no-audio` 选项)。
- **文件管理**:
    - 将处理后的原始文件移动到单独的存档目录 (可选)。
    - 标准化文件名 (可选)。
    - 清理空目录和临时文件。
- **全面的日志记录**: 生成详细的成功和错误日志，便于故障排除和监控。
- **可自定义配置**: 高级用户可以通过 Python 配置文件自定义默认设置。

### 系统要求

- Python 3.8+
- [FFmpeg](https://ffmpeg.org/download.html) 已安装并可访问 (位于系统 PATH 或指定的模块路径中)。
- [ab-av1](https://github.com/alexheretic/ab-av1/releases/) 已安装并在系统 PATH 中可访问 (视频自动 CRF 优化所需)。
- 用于音频语言检测的 `faster-whisper` 及其依赖项 (如 PyTorch) (请参阅 `requirements.txt`)。

### 安装

1.  克隆存储库:
    ```bash
    git clone https://github.com/kerasty2024/smart_encoder.git
    cd smart_encoder
    ```

2.  创建虚拟环境 (推荐) 并激活:
    ```bash
    python -m venv venv
    # Windows 系统
    venv\Scripts\activate
    # macOS/Linux 系统
    source venv/bin/activate
    ```

3.  安装所需的包:
    ```bash
    pip install -r requirements.txt
    ```

4.  **模块配置 (FFmpeg 等)**:
    *   默认情况下，脚本会尝试从系统 PATH 使用 FFmpeg 和 `ab-av1`。
    *   或者，您可以将这些可执行文件放置在特定目录中，并在 `smart_encoder/config/common.py` (`MODULE_PATH`) 中配置它们的路径。如果配置了 `MODULE_UPDATE_PATH`，脚本还支持从该路径进行自动更新。

### 使用方法

1.  **准备媒体文件**: 将要编码的视频或音频文件放置在源目录中。
2.  **运行编码器**: 在终端中导航到项目根目录 (即 `main.py` 所在的目录)。

    **标准视频编码示例:**
    ```bash
    # 使用 3 个进程编码当前目录中的视频，并在成功后移动原始文件
    python -O main.py --processes 3 --move-raw-file
    ```

    **iPhone 特定编码示例:**
    ```bash
    # 为 iPhone 编码 (假设文件位于当前目录)
    python -O main.py --iphone-specific-task --processes 1
    ```

    **纯音频编码示例 (例如，用于 iPhone 有声读物预设):**
    ```bash
    # 为 iPhone 处理音频文件 (或从视频中提取/重新编码音频)
    python -O main.py --iphone-specific-task --audio-only --move-raw-file
    ```

    **主要命令行选项:**
    *   `--processes <N>`: 使用的并行进程数 (默认值：4)。
    *   `--move-raw-file`: 成功编码后将原始源文件移动到 `_raw` 目录。
    *   `--not-rename`: 禁用自动文件名标准化。
    *   `--manual-mode`: 对视频使用手动定义的 CRF 和编码器设置 (来自配置文件)，而不是 `ab-av1` 搜索。
    *   `--allow-no-audio`: 如果视频文件没有合适的音频流，则在没有音频的情况下编码视频，而不是将其视为错误。
    *   `--log-level <LEVEL>`: 设置日志记录详细程度 (DEBUG, INFO, WARNING, ERROR)。默认值为 INFO (如果未使用 `-O` Python 标志，则为 DEBUG)。
    *   `--target-dir <PATH>`: 指定要处理的目标目录，而不是当前工作目录。
    *   有关选项的完整列表，请运行:
        ```bash
        python main.py --help
        ```

    **调试模式:**
    要在控制台中查看更详细的日志消息 (DEBUG 级别)，请在不使用 `-O` (优化) 标志的情况下运行 Python:
    ```bash
    python main.py [options...]
    ```

3.  **检查日志**:
    *   **成功日志**: 详细说明成功编码的 YAML 文件通常存储在相应的输出子目录中 (例如, `libsvtav1_encoded/.../log_YYYYMMDD_random.yaml`)。合并的日志 (`combined_log.yaml`) 会在处理目录的根目录下生成。
    *   **错误日志**: 存储在相对于处理目录的 `encode_error/<error_type_or_code>/.../error.txt` 中。
    *   **命令日志**: 执行的实际 FFmpeg 命令会记录在每个文件输出目录中的 `cmd.txt` 中。

### 配置

可以通过编辑 `smart_encoder/config/` 目录中的 Python 文件来自定义编码参数、输出目录、语言偏好和工具路径的默认设置:
*   `smart_encoder/config/common.py`: 常规路径、日志记录、语言代码。
*   `smart_encoder/config/video.py`: 视频编码默认值 (CRF、VMAF、编码器、iPhone 预设等)。
*   `smart_encoder/config/audio.py`: 音频编码默认值 (目标比特率等)。

在运行编码器之前，请根据您的需求调整这些文件。

### 贡献

欢迎贡献！如果您有改进或新功能的想法，或者发现了错误，请随时创建 issue 或提交 pull request。

### 通过赞助平台支持
- **GitHub Sponsors**: [https://github.com/sponsors/kerasty2024](https://github.com/sponsors/kerasty2024)
- **Buy Me a Coffee**: [https://buymeacoffee.com/kerasty](https://buymeacoffee.com/kerasty)

### 通过加密货币支持
通过向以下地址发送加密货币来支持该项目。每一笔贡献都有助于资助开发、服务器成本和未来的增强功能！

| 加密货币     | 地址                                                   | 二维码                                          |
|----------------|-----------------------------------------------------------|--------------------------------------------------|
| 比特币 (BTC)  | bc1qn72yvftnuh7jgjnn9x848pzhhywasxmqt5c7wp                  | ![BTC QR Code](contents/crypto/BTC_QR.jpg)       |
| 以太坊 (ETH) | 0x2175Ed9c75C14F113ab9cEaDc1890b2f87f40e78                  | ![ETH QR Code](contents/crypto/ETH_QR.jpg)       |
| Solana (SOL)   | 6Hc7erZqgreTVwCsTtNvsyzigN2oHJ4EgNGaLWtRWJ69                  | ![Solana QR Code](contents/crypto/Solana_QR.jpg) |

**如何通过加密货币贡献**:
1. 使用您的钱包应用复制地址或扫描二维码。
2. 发送任意金额以支持项目。
3. 您的贡献有助于维持该项目的活力——谢谢！

### 许可证
该项目根据 MIT 许可证授权。详情请参阅 [LICENSE](LICENSE) 文件。