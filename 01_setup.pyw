"""Double-click once to prepare this folder's environment."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from studio_launcher import entry
entry('setup')
