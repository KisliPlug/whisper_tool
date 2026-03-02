"""Paste worker — runs in a subprocess with its own COM apartment.

Reads "paste" commands from stdin and sends Ctrl+V via WScript.Shell.SendKeys
to the current foreground window.  Running in a separate process avoids
COM / message-pump issues that occur in the main app's polling loop.
"""

import sys
import win32com.client

shell = win32com.client.Dispatch("WScript.Shell")

while True:
    try:
        line = sys.stdin.readline()
        if not line:  # EOF — parent closed stdin
            break
        if line.strip() == "paste":
            shell.SendKeys("^v")
    except EOFError:
        break
