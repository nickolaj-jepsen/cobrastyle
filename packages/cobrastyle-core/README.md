# Cobrastyle-core

Core package for Cobrastyle, a CSS modules library for Python.

Currently implementing the following core functionality:
- [file resolvers](./src/cobrastyle_core/file_resolver.py): locating and loading CSS files in the filesystem, and resolving public URLs for them
- [CobraManager](./src/cobrastyle_core/manager.py): A class that manages imports, compilation and exporting of CSS modules