#!/usr/bin/env python3
"""Live tracker for parallel topotest pytest workers.

Watches every /tmp/topotests/exec-worker-N.log file produced by a parallel
topotest run and prints a continuously refreshing table that shows what
test::subtest each worker is currently running.

Markers parsed from the worker log files:
  * "Before the run (is_main: False)"          -> worker process started
  * "logstart: adding logging for <mod>.<file> on worker gwN ..."
                                               -> worker switched to a new
                                                  test module
  * "=== TEST-START: '<mod>/<file>.py::<test>'"
                                               -> a pytest subtest started
  * "=== TEST-END:   '<mod>/<file>.py::<test>'"
                                               -> a pytest subtest finished
  * "After the run (is_main: False)"           -> worker finished

Run with no args to follow /tmp/topotests forever, refreshing every second.
Press Ctrl-C to exit.
"""

from __future__ import annotations

import argparse
import glob
import math
import os
import re
import shutil
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


DEFAULT_DIR = "/tmp/topotests"
WORKER_GLOB = "exec-worker-*.log"

WORKER_RE = re.compile(r"exec-worker-(\d+)\.log$")

# 2026-04-24 15:11:34,571 DEBUG: root: logstart: adding logging for
#     <module>.<test_file> on worker gwN at /tmp/topotests/.../exec.log
LOGSTART_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+DEBUG:\s+root:\s+"
    r"logstart:\s+adding\s+logging\s+for\s+(?P<module>\S+)\s+on\s+worker\s+"
    r"gw(?P<gw>\d+)\b"
)

# 2026-04-24 15:12:54,881 DEBUG: spine-1: cmd_status(... === TEST-START: '<id>' ...
# The test id is wrapped in shell-escaped quotes ("'"'"'") so we just grab the
# id by looking for "<path>.py::<name>[<params>]" with allowed characters.
_TEST_ID = r"(?P<test>[\w./\-]+\.py::[\w\-\[\]]+)"
TEST_START_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?===\s+TEST-START:\D*?"
    + _TEST_ID
)
TEST_END_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?===\s+TEST-END:\D*?"
    + _TEST_ID
)

BEFORE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?Before the run \(is_main: False\)"
)
AFTER_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}).*?After the run \(is_main: False\)"
)

ANY_TS_RE = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


def parse_ts(s: str) -> Optional[float]:
    """Parse a topotest log timestamp into a Unix epoch float."""
    try:
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S,%f").timestamp()
    except ValueError:
        return None


@dataclass
class WorkerState:
    worker_id: int
    path: str
    pos: int = 0
    inode: Optional[int] = None
    started: bool = False
    finished: bool = False
    module: Optional[str] = None
    module_started: Optional[float] = None
    subtest: Optional[str] = None  # the function part only
    subtest_full: Optional[str] = None  # full <mod>/<file>.py::<func>
    subtest_started: Optional[float] = None
    last_event_ts: Optional[float] = None
    last_log_ts: Optional[float] = None
    tests_started: int = 0
    tests_finished: int = 0
    modules_seen: int = 0
    pending_tail: str = field(default="")  # incomplete trailing line
    # Completed subtests on this worker: list of
    # (module, subtest_full, started_ts, ended_ts).
    test_history: list[tuple[str, str, float, float]] = field(default_factory=list)

    def reset_for_module(self, module: str, ts: Optional[float]) -> None:
        self.module = module
        self.module_started = ts
        self.subtest = None
        self.subtest_full = None
        self.subtest_started = None
        self.modules_seen += 1


def iter_new_lines(state: WorkerState) -> list[str]:
    """Yield any lines newly appended to the worker log since last call."""
    try:
        st = os.stat(state.path)
    except FileNotFoundError:
        return []

    # File rotated/truncated -> restart.
    if state.inode is None:
        state.inode = st.st_ino
    elif st.st_ino != state.inode or st.st_size < state.pos:
        state.inode = st.st_ino
        state.pos = 0
        state.pending_tail = ""

    if st.st_size == state.pos:
        return []

    try:
        with open(state.path, "rb") as fh:
            fh.seek(state.pos)
            chunk = fh.read(st.st_size - state.pos)
            state.pos = fh.tell()
    except FileNotFoundError:
        return []

    text = state.pending_tail + chunk.decode("utf-8", errors="replace")
    if text.endswith("\n"):
        state.pending_tail = ""
        lines = text.split("\n")[:-1]
    else:
        # Keep the trailing partial line for the next read.
        nl = text.rfind("\n")
        if nl == -1:
            state.pending_tail = text
            return []
        state.pending_tail = text[nl + 1 :]
        lines = text[:nl].split("\n")
    return lines


def update_from_line(state: WorkerState, line: str) -> None:
    """Mutate `state` based on a single new log line."""
    m = ANY_TS_RE.match(line)
    if m:
        ts = parse_ts(m.group("ts"))
        if ts is not None:
            state.last_log_ts = ts

    m = LOGSTART_RE.match(line)
    if m:
        ts = parse_ts(m.group("ts"))
        state.started = True
        state.finished = False
        state.reset_for_module(m.group("module"), ts)
        state.last_event_ts = ts
        return

    m = TEST_START_RE.match(line)
    if m:
        ts = parse_ts(m.group("ts"))
        full = m.group("test").strip()
        if full == state.subtest_full:
            return  # echoed across many routers
        state.subtest_full = full
        state.subtest = full.split("::", 1)[1] if "::" in full else full
        state.subtest_started = ts
        state.last_event_ts = ts
        state.tests_started += 1
        return

    m = TEST_END_RE.match(line)
    if m:
        ts = parse_ts(m.group("ts"))
        full = m.group("test").strip()
        if full != state.subtest_full and state.subtest_full is None:
            return
        if full == state.subtest_full:
            state.tests_finished += 1
            if state.subtest_started is not None and ts is not None:
                state.test_history.append(
                    (state.module or "", full, state.subtest_started, ts)
                )
            state.subtest_full = None
            state.subtest = None
            state.subtest_started = None
        state.last_event_ts = ts
        return

    m = BEFORE_RE.match(line)
    if m:
        state.started = True
        state.finished = False
        state.last_event_ts = parse_ts(m.group("ts"))
        return

    m = AFTER_RE.match(line)
    if m:
        state.finished = True
        state.subtest = None
        state.subtest_full = None
        state.subtest_started = None
        state.last_event_ts = parse_ts(m.group("ts"))
        return


def discover_workers(directory: str, workers: dict[int, WorkerState]) -> None:
    for path in glob.glob(os.path.join(directory, WORKER_GLOB)):
        m = WORKER_RE.search(path)
        if not m:
            continue
        wid = int(m.group(1))
        if wid not in workers:
            workers[wid] = WorkerState(worker_id=wid, path=path)


def fmt_duration(seconds: Optional[float]) -> str:
    """Always returns a 6-character string."""
    if seconds is None or seconds < 0:
        return "     -"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds:4d}s ".rjust(6)
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m:2d}m{s:02d}s"
    h, m = divmod(m, 60)
    if h < 100:
        return f"{h:2d}h{m:02d}m"
    return f"{h}h"[-6:].rjust(6)


def truncate(text: str, width: int) -> str:
    if width <= 1:
        return text[:width]
    if len(text) <= width:
        return text
    return text[: width - 1] + "\u2026"  # ellipsis


# ANSI helpers ---------------------------------------------------------------
ESC = "\x1b["
CLEAR = ESC + "2J"
HOME = ESC + "H"
HIDE_CURSOR = ESC + "?25l"
SHOW_CURSOR = ESC + "?25h"
BOLD = ESC + "1m"
DIM = ESC + "2m"
RESET = ESC + "0m"
GREEN = ESC + "32m"
YELLOW = ESC + "33m"
CYAN = ESC + "36m"
GREY = ESC + "90m"
RED = ESC + "31m"


def status_char(s: WorkerState, now: float) -> tuple[str, str]:
    """Single-character status code + ANSI color."""
    if s.finished:
        return ("D", GREY)
    if s.subtest:
        idle = now - (s.last_log_ts or now)
        if idle > 30:
            return ("!", RED)
        return ("R", GREEN)
    if s.module:
        return ("B", YELLOW)
    if s.started:
        return ("U", CYAN)
    return ("W", GREY)


def _ansi(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


# Visible width of a cell's prefix: "WWW S TT/TT  AGEAGE " == 3+1+1+1+5+2+6+1
CELL_PREFIX_W = 20


def _format_cell(s: WorkerState, width: int, color: bool, now: float) -> str:
    ch, code = status_char(s, now)
    if s.subtest:
        age = now - (s.subtest_started or now)
        # Compact "shortmod/subtest" - mirrors pytest test id without the .py
        short_mod = (s.module or "").split(".", 1)[0]
        text = f"{short_mod}/{s.subtest}" if short_mod else s.subtest
    elif s.module:
        age = now - s.module_started if s.module_started else None
        text = s.module
    else:
        age = None
        text = "-"

    tests = f"{s.tests_finished:2d}/{s.tests_started:<2d}"  # 5
    age_s = fmt_duration(age)  # 6
    text_w = max(5, width - CELL_PREFIX_W)
    text_disp = truncate(text, text_w).ljust(text_w)
    ch_str = _ansi(ch, code, color)
    return f"{s.worker_id:3d} {ch_str} {tests}  {age_s} {text_disp}"


def _truncate_visible(text: str, width: int) -> str:
    """Truncate ignoring trailing ANSI sequences (best-effort, used for title)."""
    # Strip ANSI to measure length
    plain = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text)
    if len(plain) <= width:
        return text
    # Fall back to plain truncation
    return truncate(plain, width)


def render(workers: dict[int, WorkerState], directory: str, color: bool) -> str:
    cols, rows = shutil.get_terminal_size((140, 40))
    now = time.time()

    if not workers:
        return f"No exec-worker-*.log files found in {directory}\n"

    n = len(workers)
    running = sum(1 for s in workers.values() if s.subtest and not s.finished)
    finished = sum(1 for s in workers.values() if s.finished)
    between = sum(
        1
        for s in workers.values()
        if s.started and not s.finished and not s.subtest and s.module
    )
    stalled = sum(
        1
        for s in workers.values()
        if s.subtest and not s.finished and s.last_log_ts and (now - s.last_log_ts) > 30
    )
    total_subtests = sum(s.tests_finished for s in workers.values())

    legend = (
        f"{_ansi('R', GREEN, color)}=run "
        f"{_ansi('B', YELLOW, color)}=between "
        f"{_ansi('D', GREY, color)}=done "
        f"{_ansi('U', CYAN, color)}=setup "
        f"{_ansi('W', GREY, color)}=waiting "
        f"{_ansi('!', RED, color)}=stalled"
    )
    title = (
        f"{_ansi('topotests', BOLD, color)} {directory}  "
        f"W={n} R={running} B={between} D={finished} !={stalled}  "
        f"subtests={total_subtests}  "
        f"{datetime.now().strftime('%H:%M:%S')}  [{legend}]"
    )

    # Reserve lines for: title (1) + column header (1) + bottom section
    # (1 blank + 1 section header + TOP_N rows).
    top_n = 5
    reserved_top = 2
    reserved_bottom = 2 + top_n
    avail_rows = max(1, rows - reserved_top - reserved_bottom)
    ncols = max(1, math.ceil(n / avail_rows))
    rows_per_col = math.ceil(n / ncols)
    sep = "  "
    col_width = max(CELL_PREFIX_W + 10, (cols - (ncols - 1) * len(sep)) // ncols)

    # Header columns aligned to cell layout (worker_id 3, status 1, tests 5,
    # age 6, then text). Total prefix == CELL_PREFIX_W.
    col_header_text = "  W S TESTS    AGE TEST"
    col_header = _ansi(col_header_text.ljust(col_width), DIM, color)
    header_line = sep.join([col_header] * ncols)

    sorted_ids = sorted(workers)
    cells = [_format_cell(workers[wid], col_width, color, now) for wid in sorted_ids]

    lines = [_truncate_visible(title, cols), header_line]
    for r in range(rows_per_col):
        parts = []
        for c_idx in range(ncols):
            idx = c_idx * rows_per_col + r
            if idx < len(cells):
                parts.append(cells[idx])
            else:
                parts.append(" " * col_width)
        lines.append(sep.join(parts))

    # Bottom section: longest tests overall (completed + currently running),
    # ranked by duration. A currently-running test naturally appears here when
    # its in-flight duration exceeds those of completed tests.
    # Each entry: (duration, worker_id, status_char, status_color, module,
    #              subtest_func)
    candidates: list[tuple[float, int, str, str, str, str]] = []
    for s in workers.values():
        for mod, full, start, end in s.test_history:
            func = full.split("::", 1)[1] if "::" in full else full
            candidates.append((end - start, s.worker_id, "D", GREY, mod, func))
        if s.subtest and s.subtest_started is not None:
            ch, code = status_char(s, now)
            mod = s.module or ""
            full = s.subtest_full or s.subtest
            func = full.split("::", 1)[1] if "::" in full else full
            candidates.append(
                (now - s.subtest_started, s.worker_id, ch, code, mod, func)
            )
    candidates.sort(key=lambda c: c[0], reverse=True)
    top = candidates[:top_n]

    lines.append("")
    if top:
        section_title = (
            f"Longest {len(top)} test{'s' if len(top) != 1 else ''} "
            f"(D=done, R=running):"
        )
        lines.append(_ansi(section_title, BOLD, color))
        # Row format: "  gwNN  S   DURATN  module/subtest"
        # Visible prefix: 2 + 4 (gwNN) + 2 + 1(S) + 3 + 6 + 2 == 20
        long_prefix_w = 20
        text_w = max(10, cols - long_prefix_w)
        for dur, wid, ch, code, mod, func in top:
            ch_str = _ansi(ch, code, color)
            short_mod = mod.split(".", 1)[0]
            text = f"{short_mod}/{func}" if short_mod else func
            text = truncate(text, text_w)
            lines.append(f"  gw{wid:<3d}  {ch_str}   {fmt_duration(dur)}  {text}")
        for _ in range(top_n - len(top)):
            lines.append("")
    else:
        lines.append(_ansi(f"Longest {top_n} tests: (none observed yet)", DIM, color))
        for _ in range(top_n - 1):
            lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "-d",
        "--dir",
        default=DEFAULT_DIR,
        help=f"directory containing exec-worker-*.log files (default: {DEFAULT_DIR})",
    )
    p.add_argument(
        "-i",
        "--interval",
        type=float,
        default=1.0,
        help="refresh interval in seconds (default: 1.0)",
    )
    p.add_argument(
        "--once",
        action="store_true",
        help="parse all current logs and print a single snapshot, then exit",
    )
    p.add_argument(
        "--no-color",
        action="store_true",
        help="disable ANSI colors",
    )
    args = p.parse_args()

    if not os.path.isdir(args.dir):
        print(f"error: directory not found: {args.dir}", file=sys.stderr)
        return 1

    color = not args.no_color and sys.stdout.isatty()
    workers: dict[int, WorkerState] = {}

    def cleanup(*_: object) -> None:
        if color:
            sys.stdout.write(SHOW_CURSOR)
            sys.stdout.flush()

    signal.signal(signal.SIGINT, lambda *_: (cleanup(), sys.exit(0)))
    signal.signal(signal.SIGTERM, lambda *_: (cleanup(), sys.exit(0)))

    if color and not args.once:
        sys.stdout.write(HIDE_CURSOR)

    try:
        while True:
            discover_workers(args.dir, workers)
            for s in workers.values():
                for line in iter_new_lines(s):
                    update_from_line(s, line)

            frame = render(workers, args.dir, color)

            if args.once:
                sys.stdout.write(frame)
                sys.stdout.flush()
                return 0

            if color:
                sys.stdout.write(CLEAR + HOME + frame)
            else:
                sys.stdout.write("\n" + frame)
            sys.stdout.flush()
            time.sleep(args.interval)
    finally:
        cleanup()


if __name__ == "__main__":
    sys.exit(main())
