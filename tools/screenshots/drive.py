"""Drives a Claude Code session in a pseudo-terminal and records every byte it draws.

Usage: python3 -I drive.py <workspace> <out dir> <scenario.json>

The scenario's "model", "cols" and "rows" set up the session, and its "steps" drive it:
  {"wait": s, "min": s, "max": s}         wait until the output has been quiet for `wait` seconds
  {"type": "text"}                        type text
  {"key": "enter" | "esc" | "tab" | "down" | "up" | "ctrl-x"}
  {"mark": "name"}                        note where the stream is, for a screenshot of that moment
  {"if_present": "text", "then": [...]}   run steps if the text has appeared
  {"if_missing": "text", "then": [...]}   run steps if it has not

Text checks ignore spaces, because Claude Code draws many of them as cursor moves.
Writes <out>/raw.bin, the output stream, and <out>/marks.json, each mark's offset in it.
"""
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time

KEYS = {"enter": b"\r", "esc": b"\x1b", "tab": b"\t", "ctrl-x": b"\x18", "down": b"\x1b[B", "up": b"\x1b[A"}

# What would tie the new session to the one running this script, if there is one.
DROP = ("AI_AGENT", "CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "TERM_SESSION_ID")
DROP_PREFIX = ("CLAUDE_CODE_CHILD", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_EXECPATH", "CLAUDE_CODE_MESSAGING", "CLAUDE_CODE_SESSION")

workspace, out, scenario_file = sys.argv[1:4]
scenario = json.load(open(scenario_file))
cols, rows = scenario.get("cols", 120), scenario.get("rows", 34)

env = {k: v for k, v in os.environ.items() if k not in DROP and not k.startswith(DROP_PREFIX)}
env.update(TERM="xterm-256color", COLORTERM="truecolor")
argv = ["claude"] + (["--model", scenario["model"]] if "model" in scenario else [])

pid, fd = pty.fork()
if pid == 0:
    os.chdir(workspace)
    os.execvpe("claude", argv, env)

fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
raw = bytearray()
marks = {}
last_output = time.time()
ended = False  # the session exited; shoot.mjs checks every mark was reached


def answer_queries(chunk):
    """The replies a real terminal gives, so the session never waits on one."""
    if b"\x1b[6n" in chunk:
        os.write(fd, b"\x1b[1;1R")
    if b"\x1b[c" in chunk or b"\x1b[0c" in chunk:
        os.write(fd, b"\x1b[?62;22c")
    if b"\x1b]11;?" in chunk:
        os.write(fd, b"\x1b]11;rgb:1c1c/1c1c/1f1f\x1b\\")
    if b"\x1b]10;?" in chunk:
        os.write(fd, b"\x1b]10;rgb:e4e4/e4e4/e7e7\x1b\\")


def pump(seconds):
    global last_output, ended
    end = time.time() + seconds
    while time.time() < end and not ended:
        ready, _, _ = select.select([fd], [], [], 0.05)
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError:
            chunk = b""
        if not chunk:
            ended = True
            return
        raw.extend(chunk)
        answer_queries(chunk)
        last_output = time.time()


def seen(text):
    plain = re.sub(rb"\x1b\[[0-9;?<>=]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)", b"", bytes(raw))
    return text.replace(" ", "") in plain.decode("utf-8", "replace").replace(" ", "")


def run(steps):
    for step in steps:
        if ended:
            return
        if "wait" in step:
            start = time.time()
            while time.time() - start < step.get("max", 60) and not ended:
                pump(0.25)
                if time.time() - last_output >= step["wait"] and time.time() - start >= step.get("min", 0):
                    break
        elif "type" in step:
            for ch in step["type"]:
                os.write(fd, ch.encode())
                pump(0.02)
        elif "key" in step:
            os.write(fd, KEYS[step["key"]])
            pump(0.2)
        elif "mark" in step:
            pump(0.3)
            marks[step["mark"]] = len(raw)
            print(f"  marked {step['mark']}", flush=True)
        elif "if_present" in step:
            if seen(step["if_present"]):
                run(step["then"])
        elif "if_missing" in step:
            if not seen(step["if_missing"]):
                run(step["then"])


try:
    run(scenario["steps"])
finally:
    open(os.path.join(out, "raw.bin"), "wb").write(raw)
    json.dump(marks, open(os.path.join(out, "marks.json"), "w"))
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
