import sys
from pathlib import Path

# Add root directory to sys.path so app module is importable
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.main import create_app

app = create_app()
