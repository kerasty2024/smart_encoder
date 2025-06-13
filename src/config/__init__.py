"""
Configuration Package for the Smart Encoder.

This package centralizes all the static configuration settings for the application.
By separating configuration from the application logic, it becomes easier to manage
and modify parameters without changing the core code. This approach enhances
maintainability and allows for straightforward adjustments to the application's
behavior.

This package includes settings for:
- Audio and video file types and encoding parameters.
- Common application settings like logging formats, directory structures, and job statuses.
- User-overridable paths for external tools like FFmpeg.
- Parameters for various encoding profiles, such as standard video, audio-only, and device-specific
  (e.g., iPhone) profiles.
"""