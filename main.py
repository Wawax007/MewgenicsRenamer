import sys
import os
import logging
import traceback

# Ensure imports work when running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import MewgenicsRenamerApp


def _global_exception_handler(exc_type, exc_value, exc_tb):
    """Last-resort handler for unhandled exceptions — log and show dialog."""
    if exc_type is KeyboardInterrupt:
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return
    msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.critical(f"UNHANDLED EXCEPTION:\n{msg}")
    try:
        from tkinter import messagebox
        messagebox.showerror(
            "Unexpected Error",
            f"An unexpected error occurred:\n\n{exc_value}\n\n"
            f"Details have been written to debug.log.")
    except Exception:
        pass  # If even tkinter fails, at least we logged it


if __name__ == "__main__":
    sys.excepthook = _global_exception_handler
    app = MewgenicsRenamerApp()
    app.run()
