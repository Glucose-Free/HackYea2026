import sys
from pathlib import Path

# Appended, not prepended: the repo root holds mcp.py, which would otherwise
# shadow the installed mcp SDK that the gateway imports.
sys.path.append(str(Path(__file__).parent))
