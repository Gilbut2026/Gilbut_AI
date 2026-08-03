"""예시 요청으로 스코어링을 실행한다.

    python examples/run.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scoring import score_routes

request = json.loads((Path(__file__).parent / "request.json").read_text())
result = score_routes(request)

print(json.dumps(result, ensure_ascii=False, indent=2))
