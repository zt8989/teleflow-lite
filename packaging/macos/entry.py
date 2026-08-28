"""Launcher shim for the frozen TeleFlow.app.

PyInstaller freezes this module; it simply delegates to the real entry point in
``teleflow.app``. Kept separate from ``src`` so packaging concerns don't leak
into the library.
"""

from teleflow.app import main

if __name__ == "__main__":
    main()
