"""Double-click to launch after 01_setup.pyw has completed."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_launcher import entry
entry('start')
