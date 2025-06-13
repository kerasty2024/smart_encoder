"""
This package contains the core domain models and business logic of the Smart Encoder application.

The domain layer is the heart of the application, representing the fundamental concepts
and rules of the video/audio encoding world as this application sees it. It is designed
to be independent of other layers like the user interface (CLI), application services,
and infrastructure (file system, external tools).

This separation of concerns ensures that the core logic is clean, testable, and
not tightly coupled to specific implementation details of external dependencies.

Modules:
    exceptions.py: Defines custom exception types for specific error conditions
                   that can occur within the application, allowing for more
                   granular error handling.
    media.py: Contains the `MediaFile` class, a crucial abstraction that
              represents a media file and provides easy access to its technical
              properties by wrapping `ffprobe`.
    temp_models.py: Defines data structures for managing the state of ongoing or
                    completed tasks. The most important class here is `EncodeInfo`,
                    which makes the encoding process resilient and restartable.
"""