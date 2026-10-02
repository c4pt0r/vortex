import os
import select
import signal
import subprocess
import sys
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == 'posix', 'requires a POSIX pseudo-terminal')
class TerminalTests(unittest.TestCase):
    def test_controls_resize_and_restore(self):
        import fcntl
        import pty
        import struct
        import termios

        def resize(fd, rows, cols):
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))

        def drain(fd, duration=.15):
            data = b''
            until = time.monotonic() + duration
            while time.monotonic() < until:
                if select.select([fd], [], [], .02)[0]:
                    try:
                        data += os.read(fd, 65536)
                    except OSError:
                        break
            return data

        for mono in (False, True):
            with self.subTest(mono=mono):
                master, slave = pty.openpty()
                process = None
                try:
                    resize(slave, 24, 80)
                    before = termios.tcgetattr(slave)
                    command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'vortex.py')]
                    if mono:
                        command += ['--mono', '--no-hud']
                    process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                               env={**os.environ, 'TERM': 'xterm-256color'})
                    output = drain(master, .8)
                    self.assertIsNone(process.poll())
                    self.assertGreater(len(output), 100)
                    os.write(master, b' ')
                    output += drain(master, .3)
                    self.assertEqual(drain(master), b'')
                    # Focus reports (sent whole, as a terminal does) must not read as Esc.
                    for report in (b'\x1b[O', b'\x1b[I'):
                        os.write(master, report)
                        output += drain(master)
                        self.assertIsNone(process.poll())
                    for key in b'omcjsvktylwnx19r+hp[]ip]i\t\x1b':
                        os.write(master, bytes([key]))
                        output += drain(master)
                        self.assertIsNone(process.poll())
                    for rows, cols in ((1, 1), (32, 100)):
                        resize(slave, rows, cols)
                        os.kill(process.pid, signal.SIGWINCH)
                        output += drain(master)
                        self.assertIsNone(process.poll())
                    if mono:
                        os.kill(process.pid, signal.SIGINT)
                    else:
                        os.write(master, b'q')
                    output += drain(master, .3)
                    self.assertEqual(process.wait(timeout=5), 0)
                    after = termios.tcgetattr(slave)
                    # macOS may set this transient kernel retype-input flag.
                    before[3] &= ~getattr(termios, 'PENDIN', 0)
                    after[3] &= ~getattr(termios, 'PENDIN', 0)
                    self.assertEqual(before, after)
                    self.assertNotIn(b'Traceback', output)
                finally:
                    if process is not None and process.poll() is None:
                        process.kill()
                        process.wait()
                    os.close(master)
                    os.close(slave)
