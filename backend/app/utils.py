import gzip
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


@contextmanager
def open_text(path: Path, encoding: str="utf-8") -> Iterator[TextIO]:
    if path.suffix == ".gz":
        f = gzip.open(path, "rt", encoding=encoding)
    else:
        f = path.open("r", encoding=encoding)
    try:
        yield f
    finally:
        f.close()