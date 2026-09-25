# Double-click to start the client without a console window (uses pythonw).
import runpy
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
runpy.run_module("client.main", run_name="__main__")
