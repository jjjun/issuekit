"""Shared signal handler installation helpers."""

from __future__ import annotations

import signal
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager


@contextmanager
def installed_signal_handlers(
    handlers: Mapping[int, Callable[..., object]],
) -> Iterator[None]:
    previous: dict[int, signal.Handlers] = {}
    for signum, handler in handlers.items():
        try:
            previous_handler = signal.getsignal(signum)
            signal.signal(signum, handler)
        except (ValueError, OSError, AttributeError):
            continue
        previous[signum] = previous_handler
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (ValueError, OSError, AttributeError):
                pass
