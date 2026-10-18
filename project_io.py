"""Bounded reads for frequently polled application output."""
from pathlib import Path


def log_tail(path, characters=24000):
    try:
        with Path(path).open('rb') as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - characters * 4))
            return stream.read(characters * 4).decode('utf-8', errors='replace')[-characters:]
    except FileNotFoundError:
        return ''
