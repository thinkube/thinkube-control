# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""What a run prints, sent as its log.

The run's standard output and standard error are replaced by a pipe at the
level of the process, so the output of libraries and of child processes is
taken too. A thread reads the pipe, writes everything on to where the
output went before, and sends it on in pieces.
"""

import codecs
import os
import select
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

# Seconds between two pieces of the log, and the size at which a piece is sent at once
PIECE_EVERY = 1.0
PIECE_SIZE = 32_000


class LogTee:
    def __init__(self, send: Callable[[str, Dict[str, Any]], None]) -> None:
        self._send = send
        self.error: Optional[Exception] = None
        self._pieces = 0
        sys.stdout.flush()
        sys.stderr.flush()
        self._out = os.dup(1)
        self._err = os.dup(2)
        self._read, write = os.pipe()
        os.dup2(write, 1)
        os.dup2(write, 2)
        os.close(write)
        self._thread = threading.Thread(target=self._pump, daemon=True, name="mill-log")
        self._thread.start()

    def _pump(self) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        text = ""
        sent_at = time.monotonic()
        ended = False
        while not ended:
            ready, _, _ = select.select([self._read], [], [], PIECE_EVERY)
            if ready:
                data = os.read(self._read, 65536)
                if data:
                    os.write(self._out, data)
                    text += decoder.decode(data)
                else:
                    ended = True
                    text += decoder.decode(b"", final=True)
            due = ended or len(text) >= PIECE_SIZE or time.monotonic() - sent_at >= PIECE_EVERY
            if text and due:
                self._piece(text)
                text = ""
                sent_at = time.monotonic()

    def _piece(self, text: str) -> None:
        if self.error is not None:
            return
        self._pieces += 1
        try:
            self._send("log", {"seq": self._pieces, "text": text})
        except Exception as e:
            # The output still goes where it went before; close() says the log is not whole
            self.error = e

    def close(self) -> None:
        """Give the process its output back, and send what is left of the log."""
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(self._out, 1)
        os.dup2(self._err, 2)
        # The pipe ends when nothing holds its other end; a child process still running may
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            os.close(self._read)
            os.close(self._out)
        os.close(self._err)
