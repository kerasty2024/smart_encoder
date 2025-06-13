"""
Services Package for the Smart Encoder Application.

This package contains the "service layer" of the application. In this architecture,
a service is a class designed to perform a specific, high-level task or coordinate
a piece of business logic. These services act as a bridge between the high-level
pipelines (the "how" and "when" of processing) and the lower-level domain models
and infrastructure (the "what" and "with what").

The primary responsibilities of services in this application include:

- **Encoding Services (`VideoEncoder`, `AudioEncoder`, etc.):**
  These are the core services responsible for taking a media file and processing it
  with FFmpeg. They handle everything from building the command, executing it,
  managing state (with `EncodeInfo`), and handling success or failure.

- **Preprocessing Service (`PreVideoEncoder`):**
  This service analyzes a media file *before* the main encoding begins. It makes
  decisions about whether to skip the file, which streams to use, and what
e  ncoding quality settings are optimal.

- **File Processing Service (`ProcessFiles` and its subclasses):**
  This service is responsible for discovering media files in the source directory,
  filtering them based on type, and performing initial cleanup tasks like
  standardizing filenames or removing invalid files.

- **Logging Service (`SuccessLog`, `ErrorLog`):**
  This service provides a structured way to write log files for both successful
  encodes (in a structured YAML format) and errors (in plain text), which is
  separate from the real-time console logging.

By encapsulating logic within these services, the application becomes more modular,
testable, and easier to maintain.
"""