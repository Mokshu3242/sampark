import ctypes
import logging

log = logging.getLogger("flood-watch.mem")

try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:                 # Windows / macOS: no-op
    _libc = None


def trim(tag: str = "") -> None:
    """Hand freed heap memory back to the OS (glibc only)."""
    import gc
    gc.collect()
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass
    if tag:
        from pipeline.hydrology import rss_mb
        log.info(f"Trimmed after {tag}. Memory {rss_mb()} MB")