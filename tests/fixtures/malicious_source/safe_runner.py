from pathlib import Path
import json
import sys

Path(sys.argv[1]).write_text(json.dumps({"value": 1.25}), encoding="utf-8")
