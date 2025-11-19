"""
Rich UI runtime helper.

Runs the Rich dashboard inside a dedicated thread so that the Live console
can refresh continuously without fighting with asyncio schedulers.
"""

from __future__ import annotations

import queue
import threading
import time
from typing import Optional

from .ui_rich import Dashboard, map_bot_to_state, BotState
from .logger import get_logger


class RichUIRuntime:
    """
    Dedicated Rich UI runtime.

    The dashboard lives in its own thread and receives BotState updates via
    a size-1 queue (latest update always wins).
    """

    def __init__(self, refresh_per_second: float = 4.0):
        self.refresh_per_second = refresh_per_second
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._queue: queue.Queue[BotState] = queue.Queue(maxsize=1)
        self._error: Optional[Exception] = None
        self._dashboard: Optional[Dashboard] = None
        self._log = get_logger("UI")

    # ──────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────
    def start(self, initial_state: BotState) -> None:
        """
        Start the UI thread and render the initial state.
        """
        if self._thread and self._thread.is_alive():
            return

        self._stop.clear()
        self._ready.clear()
        self._error = None

        self._thread = threading.Thread(
            target=self._run,
            name="RichUIRuntime",
            args=(initial_state,),
            daemon=True,
        )
        self._thread.start()

        # Wait for the UI thread to signal readiness (or failure)
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("Rich UI thread did not start in time")
        if self._error:
            raise self._error

    def update(self, state: BotState) -> None:
        """
        Submit a new state to the UI thread.
        """
        if not self._thread or not self._thread.is_alive():
            return

        # Non-blocking put – drop older state if queue is full
        try:
            self._queue.put_nowait(state)
        except queue.Full:
            try:
                _ = self._queue.get_nowait()
            except queue.Empty:
                pass
            finally:
                try:
                    self._queue.put_nowait(state)
                except queue.Full:
                    # If we still can't enqueue, skip this update
                    self._log.warning("[UI] Dropping UI update – queue saturated")

    def stop(self) -> None:
        """
        Stop the UI thread and clean up resources.
        """
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._ready.clear()

    # ──────────────────────────────────────────────────────────
    # Internal worker
    # ──────────────────────────────────────────────────────────
    def _run(self, initial_state: BotState) -> None:
        try:
            self._dashboard = Dashboard(refresh_per_second=self.refresh_per_second)
            self._dashboard.__enter__()
            self._dashboard.update(initial_state)
            self._ready.set()

            poll_interval = 1.0 / max(self.refresh_per_second, 1.0)

            while not self._stop.is_set():
                try:
                    state = self._queue.get(timeout=poll_interval)
                    if state is None:
                        continue
                    self._dashboard.update(state)
                except queue.Empty:
                    continue
                except Exception as e:
                    self._log.error(f"[UI] Runtime update failed: {type(e).__name__}: {e}", exc_info=True)
        except Exception as exc:
            self._error = exc
            self._ready.set()
        finally:
            try:
                if self._dashboard:
                    self._dashboard.__exit__(None, None, None)
            finally:
                self._dashboard = None
                self._ready.set()


def create_initial_state(bot) -> BotState:
    """
    Helper to build the initial BotState for the runtime.
    """
    return map_bot_to_state(bot)


