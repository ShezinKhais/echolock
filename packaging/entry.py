"""Entry point for the packaged executable.

The .exe opens the desktop interface, because someone who downloaded a binary
rather than the source is not looking for a command line. The subcommands are
still reachable by passing arguments, so the same file serves both:

    EchoLock.exe                  opens the window
    EchoLock.exe check            runs the command-line check
    EchoLock.exe --help           lists everything
"""

import multiprocessing
import sys

from echolock.cli import main

if __name__ == "__main__":
    # A frozen build re-executes itself to spawn processes, so without this a
    # child would relaunch the whole interface instead of doing its work.
    multiprocessing.freeze_support()
    sys.exit(main(sys.argv[1:] or ["gui"]))
