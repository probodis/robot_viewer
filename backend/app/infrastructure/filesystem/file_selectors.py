from pathlib import Path

class BaseFileSelector:
    """
    Base class for selecting a file from a dictionary of files based on file prefixes and suffixes.

    Args:
        files (dict[str, Path]): Mapping from relative file path to Path object.

    Returns:
        Path | None: Path to the selected file or None if no matching file is found.

    Subclasses can define PREFIXES and SUFFIXES for flexible file selection.
    """

    PREFIXES: tuple[str, ...] = tuple()
    SUFFIXES: tuple[str, ...] = tuple()

    def select(self, files: dict[str, Path]) -> Path | None:
        """
        Selects a file whose relative path starts with any of PREFIXES and ends with any of SUFFIXES.

        Args:
            files (dict[str, Path]): Mapping from relative file path to Path object.

        Returns:
            Path | None: Path to the selected file or None if no matching file is found.
        """
        # Check both prefix and suffix for each file
        for rel_path, path in files.items():
            # If PREFIXES is empty, skip prefix check
            prefix_ok = not self.PREFIXES or rel_path.startswith(self.PREFIXES)
            # If SUFFIXES is empty, skip suffix check
            suffix_ok = not self.SUFFIXES or rel_path.endswith(self.SUFFIXES)
            if prefix_ok and suffix_ok:
                return path
        return None


class TelemetryFileSelector(BaseFileSelector):
    PREFIXES = ("start_order",)
    SUFFIXES = ("_start_order.txt", "_start_order.txt.gz")


class OrderFileSelector(BaseFileSelector):
    PREFIXES = ("orders",)
    SUFFIXES = ("_orders.txt", "_orders.txt.gz")


class SauceWeightFileSelector(BaseFileSelector):
    PREFIXES = ("sauce_weight",)
    SUFFIXES = ("_sauce_weight.txt", "_sauce_weight.txt.gz")
