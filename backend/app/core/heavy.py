"""One heavy job at a time (image render, flow trace). Stops parallel peaks on a 512 MB box."""
import threading

HEAVY = threading.Semaphore(1)