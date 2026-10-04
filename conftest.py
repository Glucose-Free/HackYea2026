import sys
from pathlib import Path

# The policy engine (policy_middleware.py, examples/) lives at the repo root, outside the installed package.
sys.path.append(str(Path(__file__).parent))
