import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from pprint import pformat
from typing import Optional, List, Dict, Tuple

from loguru import logger

from scripts.controllers.functions import (
    run_cmd,
    format_timedelta,
    detect_audio_language_multi_segments,
)
from scripts.models.MediaFile import MediaFile
from scripts.models.PreVideoEncodeExceptions import (
    CRFSearchFailedException,
    SkippedVideoFileException,
    UnexpectedPreEncoderException,
    NoAudioStreamException, FileAlreadyEncodedException, FileOversizedException, BitRateTooLowException,
    FormatExcludedException, NoStreamsFoundException,
)
from scripts.models.TempFile import EncodeInfo
from scripts.settings.common import BASE_ERROR_DIR, LANGUAGE_WORDS
from scripts.settings.video import (
    VIDEO_OUT_DIR_ROOT,
    VIDEO_COMMENT_ENCODED,
    VIDEO_OVER_SIZE_TAG_PRE_ENCODE,
    VIDEO_BITRATE_LOW_THRESHOLD,
    EXCEPT_FORMAT,
    SAMPLE_EVERY,
    MAX_ENCODED_PERCENT,
    TARGET_VMAF,
    AV1_ENCODER,
    MANUAL_CRF,
    SKIP_VIDEO_CODEC_NAMES,
    ENCODERS,
    VIDEO_NO_AUDIO_FOUND_ERROR_DIR,
)


class PreEncoder:
    """
    Base class for handling pre-encoding operations.
    This class should be inherited to implement specific encoding logic.

    Attributes:
        media_file (MediaFile): The media file to be processed.
        start_time (datetime): The time when encoding started.
        comment_encoded (str): Tag used for identifying encoded files.
        encoded_dir (Path): Directory where encoded files are saved.
        skip_log (Path): Path to the log file for skipped files.
        error_dir (Path): Directory for error logs and files.
        renamed_file (Path): Path to the file renamed due to errors.
        encode_stream_count (int): Number of streams in the media file.
        over_sized_tags (List[str]): Tags indicating potential oversized files.
        bit_rate (int): Bit rate of the media file.
        bit_rate_threshold (int): Threshold below which files are skipped.
        manual_mode (bool): Flag indicating if encoding is in manual mode.
        md5 (str): MD5 hash of the media file.
        sha256 (str): SHA256 hash of the media file.
        encode_info (EncodeInfo): Object holding encoding metadata.
    """

    # For PreVideoEncoder
    best_encoder: str = ""
    best_crf: int = 0
    output_video_streams: List
    output_audio_streams: List
    output_subtitle_streams: List
    crf_checking_time: timedelta = None
    best_ratio: float = None
    renamed_file: Path = None

    def __init__(
            self, media_file: Optional[MediaFile] = None, manual_mode: bool = False
    ):
        """
        Initializes the PreEncoder object.

        :param media_file: The media file to process. If None, no media file is set.
        :param manual_mode: Flag to enable manual mode, affecting encoding decisions.
        """
        self.media_file = media_file
        self.start_time = datetime.now()
        self.comment_encoded = ""
        self.encoded_dir = (
            Path("")
            if media_file is None
            else Path(VIDEO_OUT_DIR_ROOT)
                 / Path(Path(media_file.path).parent.relative_to(Path.cwd()))
        )
        self.skip_log = (
            self.encoded_dir / Path("skipped.txt") if media_file else Path("")
        )
        self.error_dir = Path(BASE_ERROR_DIR)

        self.encode_stream_count = 0
        self.over_sized_tags = []
        self.bit_rate = 0
        self.bit_rate_threshold = VIDEO_BITRATE_LOW_THRESHOLD
        self.manual_mode = manual_mode
        self.md5 = ""
        self.sha256 = ""
        self.encode_info = (
            EncodeInfo(self.media_file.md5) if media_file else EncodeInfo("")
        )

    def start(self):
        """
        Starts the pre-encoding process. This includes checking if the file should be skipped.
        """
        if self.media_file:
            self.skip_unneeded_file()

    def skip_unneeded_file(self):
        """
        Evaluates whether the media file should be skipped based on various criteria.

        Raises:
            FileAlreadyEncodedException: If the file is already encoded.
            FileOversizedException: If the file will be oversized when encoded and manual mode is off.
            BitRateTooLowException: If the file's bitrate is below the threshold.
            FormatExcludedException: If the file's format is excluded from processing.
            NoStreamsFoundException: If no streams are found in the media file.

        On raising any of these exceptions, the file is moved to the appropriate directory and
        logged. The exceptions are then caught and handled, with the file being renamed and moved
        to the appropriate directory.
        """
        if not self.media_file:
            return

        try:
            if self.comment_encoded in self.media_file.comment:
                raise FileAlreadyEncodedException(
                    f"Skipped because already encoded: {self.media_file.path}"
                )
            elif self.bit_rate <= self.bit_rate_threshold:
                raise BitRateTooLowException(
                    f"Skipped because bitrate below threshold ({VIDEO_BITRATE_LOW_THRESHOLD}): {self.media_file.path}"
                )
            elif self.media_file.vcodec in EXCEPT_FORMAT:
                raise FormatExcludedException(
                    f"Skipped because format is excluded ({self.media_file.vcodec}): {self.media_file.path}"
                )
            elif self.encode_stream_count == 0:
                raise NoStreamsFoundException(
                    f"No streams found in: {self.media_file.path}"
                )

        except (
                FileAlreadyEncodedException, FileOversizedException, BitRateTooLowException,
                FormatExcludedException) as e:
            log_word = str(e)
            with self.skip_log.open("a", encoding="utf-8") as log_file:
                log_file.write(log_word + "\n")
            self.encoded_dir.mkdir(parents=True, exist_ok=True)
            self.renamed_file = self.encoded_dir / self.media_file.filename
            self.renamed_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(self.media_file.path, self.renamed_file)
        except NoStreamsFoundException:
            logger.error(f"No streams found in: {self.media_file.path}")
            self.media_file.load_failed_dir.mkdir(parents=True, exist_ok=True)
            self.renamed_file = (
                    self.media_file.load_failed_dir / self.media_file.filename
            )
            self.renamed_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(self.media_file.path, self.renamed_file)

    def set_suitable_codec_options(self):
        """
        Placeholder method for setting codec options suitable for the media file.

        This method should be overridden in subclasses to define specific codec options.
        """
        pass

    def move_error_file(self, dir_name: str):
        """
        Move the file to an error directory for further analysis.

        Args:
            dir_name (str): Name of the directory to move the file.
            media_file (MediaFile): The media file to move.
        """
        self.error_dir = Path(BASE_ERROR_DIR) / Path(dir_name) / self.media_file.relative_dir
        self.renamed_file = self.error_dir / self.media_file.filename
        self.error_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(self.media_file.path, self.renamed_file)


class PreVideoEncoder(PreEncoder):
    """
    A class for pre-encoding video files to optimize their codec and bitrate.

    Attributes:
        best_crf (int): The best CRF value found for encoding.
        best_encoder (str): The best encoder determined for the media file.
        best_ratio (float): The best encoded ratio determined for the media file.
    """

    def __init__(
            self, media_file: Optional[MediaFile] = None, manual_mode: bool = False
    ):
        """
        Initialize the PreVideoEncoder with a media file and optional manual mode.

        Args:
            media_file (Optional[MediaFile]): The media file to be processed.
            manual_mode (bool): Flag indicating if manual mode should be used.
        """
        super().__init__(media_file, manual_mode)
        if media_file:
            # Set up directories and parameters for encoding
            self.encoded_dir = Path(VIDEO_OUT_DIR_ROOT) / Path(
                media_file.path
            ).parent.relative_to(Path.cwd())
            self.encoders: Tuple[str] = ENCODERS
            self.comment_encoded = VIDEO_COMMENT_ENCODED
            self.encode_stream_count = media_file.video_stream_count
            self.over_sized_tags = [
                VIDEO_OVER_SIZE_TAG_PRE_ENCODE,
                VIDEO_COMMENT_ENCODED,
                "encoded",
            ]
            self.bit_rate = media_file.vbitrate
            self.bit_rate_threshold = VIDEO_BITRATE_LOW_THRESHOLD
            self.output_video_streams: List[Dict] = []
            self.output_audio_streams: List[Dict] = []
            self.output_subtitle_streams: List[Dict] = []

    def start(self):
        """
        Start the pre-encoding process by determining the best CRF and encoder.
        """
        super().start()
        if self.encode_info.load():
            # Load encoding information if available
            self.best_crf = self.encode_info.crf
            self.best_encoder = self.encode_info.encoder
            self.manual_mode = True
            return


        if self.manual_mode:
            # Use manual mode settings if enabled
            self.best_crf = MANUAL_CRF
            self.best_encoder = self.encoders[0]
            self.set_output_streams()
            self.crf_checking_time = timedelta(microseconds=0)
            return

        try:
            self.set_output_streams()
        except NoAudioStreamException as nase:
            logger.error(nase)
            self.move_error_file(
                VIDEO_NO_AUDIO_FOUND_ERROR_DIR.name)
            return

        self.set_suitable_codec_options()

    def set_suitable_codec_options(self):
        """
        Determine the suitable codec options for encoding.
        This method tries to find the best CRF and encoder combination for the media file.
        """
        crf_check_start_time = datetime.now()
        self.best_ratio = 101  # Initialize the best ratio with a high value

        for encoder in self.encoders:
            try:
                # Check CRF for each encoder and update the best CRF and encoder
                crf, encoded_ratio = self.check_crf(encoder)
                if encoded_ratio < self.best_ratio:
                    self.best_encoder = encoder
                    self.best_crf = crf
                    self.best_ratio = encoded_ratio
            except CRFSearchFailedException:
                # Handle CRF search failure
                if not self.best_encoder:
                    self.best_encoder = self.encoders[0]
                if not self.best_crf:
                    self.best_crf = MANUAL_CRF
                    self.manual_mode = True
            except Exception as e:
                # Log unexpected errors and move the file to an error directory
                logger.error('unexpected error!')
                logger.error(e)
                self.move_error_file(str(type(e)))

        crf_check_end_time = datetime.now()
        self.crf_checking_time = crf_check_end_time - crf_check_start_time
        logger.debug(
            f"{self.media_file.path}, CRF checking time: {format_timedelta(self.crf_checking_time)}"
        )

    def check_crf(self, encoder: str = AV1_ENCODER) -> Tuple[int, int]:
        """
        Perform CRF (Constant Rate Factor) search to find optimal CRF and encoded ratio.

        Args:
            encoder (str): The encoder to be used for CRF search.

        Returns:
            Tuple[int, int]: The CRF value and encoded ratio.

        Raises:
            CRFSearchFailedError: If the CRF search fails.
            SkippedVideoFileError: If the file was marked as skipped.
        """
        crf_not_matched = 800  # Default value when CRF is not matched
        encoded_ratio_not_matched = (
            200  # Default value when encoded ratio is not matched
        )

        if self.renamed_file:  # Skip processing if the file was renamed
            raise SkippedVideoFileException(f"no need to pre-encode: {self.renamed_file}")

        # Construct command for CRF search
        cmd = (
            f'ab-av1 crf-search -e {encoder} -i "{self.media_file.path}" '
            f"--sample-every {SAMPLE_EVERY} --max-encoded-percent {MAX_ENCODED_PERCENT} "
            f"--min-vmaf {TARGET_VMAF}"
        )
        res = run_cmd(cmd, self.media_file.path, self.error_dir)

        if res is None:
            raise CRFSearchFailedException(
                f"CRF check failed for file: {self.media_file.path}"
            )

        elif res.returncode == 0:
            # Parse the output for CRF and encoded ratio
            stdout = res.stdout
            crf_match = re.search(r"crf (\d+)", stdout.lower())
            ratio_match = re.search(r"(\d+)%", stdout.lower())
            crf = int(crf_match.group(1)) if crf_match else crf_not_matched
            encoded_ratio = (
                int(ratio_match.group(1)) if ratio_match else encoded_ratio_not_matched
            )
            logger.debug(
                f"{self.media_file.path}, {encoder}: CRF {crf}, Ratio: {encoded_ratio}"
            )

            if crf == crf_not_matched or encoded_ratio == encoded_ratio_not_matched:
                raise CRFSearchFailedException(
                    f"CRF check failed for file: {self.media_file.path}"
                )
            return crf, encoded_ratio

        elif res.returncode == 1:
            raise CRFSearchFailedException(
                f"CRF check failed for file: {self.media_file.path}"
            )
        else:
            raise UnexpectedPreEncoderException(
                f"Unexpected error: {self.media_file.path}, "
                f"return code: {res.returncode}",
                f"{res.stdout}, {res.stderr}",
            )

    def set_output_streams(self):
        """
        Set the output streams for video, audio, and subtitles.
        """
        self.set_output_video_streams()
        self.set_output_subtitle_streams()
        self.set_output_audio_streams()

    def set_output_video_streams(self):
        """
        Configure the output video streams based on the media file streams.
        Only include streams with a valid frame rate and codec name.
        """
        if len(self.media_file.video_streams) == 1:
            self.output_video_streams = self.media_file.video_streams
            return
        self.output_video_streams = [
            video_stream
            for video_stream in self.media_file.video_streams
            if "avg_frame_rate" in video_stream
               and "codec_name" in video_stream
               and video_stream["codec_name"] not in SKIP_VIDEO_CODEC_NAMES
        ]

    def set_output_audio_streams(self):
        """
        Configure the output audio streams based on the media file streams.
        Only include streams with a valid sample rate or language.
        """
        sample_rate_threshold = 1000  # Minimum sample rate threshold
        self.output_audio_streams = []
        if len(self.media_file.audio_streams) == 1:
            self.output_audio_streams = self.media_file.audio_streams
            return
        elif len(self.media_file.audio_streams) == 0:
            raise NoAudioStreamException(
                f"No suitable audio stream found for file: {self.media_file.path}"
            )
        logger.debug(len(self.media_file.audio_streams))
        for stream in self.media_file.audio_streams:
            # Process audio streams based on their sample rate and language
            logger.debug(pformat(stream))
            if len(self.media_file.audio_streams) <= 1 or "sample_rate" in stream:
                if "sample_rate" in stream:
                    sample_rate = int(float(stream.get("sample_rate")))
                    if sample_rate < sample_rate_threshold:
                        continue
                if self._is_valid_audio_stream(stream):
                    self.output_audio_streams.append(stream)

        if not self.output_audio_streams:
            raise NoAudioStreamException(
                f"No suitable audio stream found for file: {self.media_file.path}"
            )

    def _is_valid_audio_stream(self, stream: Dict) -> bool:
        """
        Check if an audio stream is valid based on language criteria.

        Args:
            stream (Dict): The audio stream information.

        Returns:
            bool: True if the stream is valid, False otherwise.
        """
        if "language" in stream:
            # Check if the language in the stream matches the desired languages
            return any(
                language_word.lower() in stream["language"].lower()
                for language_word in LANGUAGE_WORDS
            )

        for key in stream.keys():
            # Check nested keys for language information
            if isinstance(stream.get(key), dict) and "language" in stream.get(key):
                return any(
                    language_word.lower() in stream.get(key)["language"].lower()
                    for language_word in LANGUAGE_WORDS
                )

        # Detect language based on audio segments if not explicitly set
        detected_language = detect_audio_language_multi_segments(
            self.media_file.path, stream, duration=self.media_file.duration
        )
        return any(
            language_word in detected_language for language_word in LANGUAGE_WORDS
        )

    def set_output_subtitle_streams(self):
        """
        Configure the output subtitle streams based on the media file streams.
        Only include streams with a valid language.
        """
        if len(self.media_file.subtitle_streams) <= 1:
            self.output_subtitle_streams = self.media_file.subtitle_streams
            return
        self.output_subtitle_streams = [
            stream
            for stream in self.media_file.subtitle_streams
            if "language" in stream
               and any(
                language_word in stream["language"].lower()
                for language_word in LANGUAGE_WORDS
            )
        ]
