import os
import sys

ROOT = os.path.join(os.path.dirname(__file__), os.pardir)
PLUGIN = os.path.join(ROOT, "rplugin", "python3")

sys.path.append(os.path.abspath(PLUGIN))
