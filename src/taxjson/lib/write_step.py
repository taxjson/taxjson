"""A command's writes as one step, all or nothing: close-year's freeze of the
inputs, its reports as filed and its lock, the lock written last (backlog
TJ-4627).

Each write's undo step is added before the write, so whatever stops the
command between them can take back what was written; the file written last
is the commit point, told by its identity (`file_id`) wherever the command
stopped. `settle` runs the steps to the end, and `hold_interrupts` makes a
Ctrl-C pressed again wait for that instead of stopping it halfway: a second
Ctrl-C stopped close-year's undo and left the year half filed. A Ctrl-C held,
or one whose KeyboardInterrupt some code swallowed, is never lost: the
command raises it (`raise_held`) before its commit and at its end. Standard
library only.
"""
import contextlib
import signal
import threading
from pathlib import Path
from typing import Any, Callable, List, Optional, Tuple


def file_id(path: Path) -> Optional[Tuple[int, int, int]]:
    """A file's inode, size and time (None: no file): a write that
    replaces the file (a rename over it) changes it."""
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_ino, st.st_size, st.st_mtime_ns)


class WriteStep:
    """`undo`: the steps that take back each write made before the commit
    file, each added before the write it takes back; `done`: the steps once
    it is written (a folder it replaced removed). `commit` and `commit_was`
    (its `file_id` before the first write) tell whether it was written;
    `committed`, that its write returned. `stopping`: a Ctrl-C now waits
    (`hold_interrupts`); `pressed`: one came in (`raise_held`)."""

    def __init__(self) -> None:
        self.undo: List[Callable[[], None]] = []
        self.done: List[Callable[[], None]] = []
        self.commit: Optional[Path] = None
        self.commit_was: Optional[Tuple[int, int, int]] = None
        self.committed = self.stopping = self._held = self.pressed = False

    def begin(self, commit: Path) -> None:
        """Before the first write: the commit file as it is."""
        self.commit_was = file_id(commit)
        self.commit = commit

    def written(self) -> bool:
        """The commit file is written by this step."""
        return self.committed or (self.commit is not None
                                  and file_id(self.commit) != self.commit_was)

    def settle(self, *, stopping: bool = True) -> bool:
        """Run the steps for where the command is (`done` once the commit
        file is written, else `undo`), the last added first, each to the
        end: one an interrupt stops runs again (each can run twice), and
        one that fails leaves the rest to run. `stopping` (an exception is
        on its way, which the caller raises again): an interrupt meanwhile
        is let go; else the first one is raised once all ran. True when
        the commit file is written."""
        self.stopping = self.stopping or stopping
        first: Optional[KeyboardInterrupt] = None
        written = False
        for _ in range(100):
            try:
                written = self.written()
                steps = self.done if written else self.undo
                while steps:
                    with contextlib.suppress(OSError):
                        steps[-1]()
                    steps.pop()
                break
            except KeyboardInterrupt as e:
                first = first or e
        if first is not None and not stopping:
            raise first
        return written

    def hold_interrupts(self) -> None:
        """Ctrl-C stops the command once: pressed again, or while it puts
        back what it wrote (`settle`), it waits for that to end. Only over
        Python's own Ctrl-C handler, in the main thread; `release` puts
        that one back."""
        if (threading.current_thread() is not threading.main_thread()
                or signal.getsignal(signal.SIGINT) is not signal.default_int_handler):
            return

        def _sigint(signum: int, frame: Any) -> None:
            self.pressed = True
            if not self.stopping:
                self.stopping = True
                raise KeyboardInterrupt

        self._held = True           # (first: `release` puts it back)
        signal.signal(signal.SIGINT, _sigint)

    def raise_held(self) -> None:
        """The command goes on (to its commit, or its end) after a Ctrl-C:
        one held, or one raised that some code swallowed on its way. It is
        raised now, so it is never lost: one stopped the command once, and
        a swallowed one left every later Ctrl-C held for good."""
        if self.pressed:
            self.pressed = False
            raise KeyboardInterrupt

    def release(self) -> None:
        """The command has ended: a Ctrl-C now is the process's again."""
        self.stopping = True
        if self._held:
            signal.signal(signal.SIGINT, signal.default_int_handler)
