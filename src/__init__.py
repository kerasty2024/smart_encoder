"""
This file marks the 'src' directory as a Python package.

By convention, an __init__.py file in a directory tells the Python interpreter
that the directory should be treated as a package, allowing you to import modules
from it.

This file can be empty, as it is here. Alternatively, it can be used for several
purposes, such as:
1.  Package-level initializations: Code that should run when the package is first
    imported.
2.  Convenience imports: You can import key classes or functions from submodules
    to make them accessible directly from the package level. For example:

    from .pipeline.video_pipeline import StandardVideoPipeline
    from .domain.media import MediaFile

    This would allow other parts of the application to use `from src import StandardVideoPipeline`
    instead of the longer `from src.pipeline.video_pipeline import StandardVideoPipeline`.

For now, this file is intentionally left simple to serve its primary purpose of
defining the 'src' package.
"""