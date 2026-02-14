import sys
import os

# Ensure imports work when running from any directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import MewgenicsRenamerApp

if __name__ == "__main__":
    app = MewgenicsRenamerApp()
    app.run()
