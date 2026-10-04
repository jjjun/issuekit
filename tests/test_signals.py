import signal

import pytest

from issuekit.signals import installed_signal_handlers


def test_installed_signal_handlers_restore_previous_handlers_after_exception() -> None:
    signum = signal.SIGUSR1
    previous = signal.getsignal(signum)

    def handler(_signum: int, _frame: object) -> None:
        return None

    with pytest.raises(RuntimeError, match="body failed"):
        with installed_signal_handlers({signum: handler}):
            assert signal.getsignal(signum) is handler
            raise RuntimeError("body failed")

    assert signal.getsignal(signum) is previous
