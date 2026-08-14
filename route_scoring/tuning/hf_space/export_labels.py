from __future__ import annotations

import csv
import json
import os
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "").strip()

if not HF_TOKEN or not HF_DATASET_REPO:
    raise RuntimeError("HF_TOKEN과 HF_DATASET_REPO 환경변수가 필요합니다.")

api = HfApi(token=HF_TOKEN)
files = api.list_repo_files(
    repo_id=HF_DATASET_REPO,
    repo_type="dataset",
    token=HF_TOKEN,
)

latest = {}
for filename in files:
    if not filename.startswith("responses/") or not filename.endswith(".json"):
        continue
    local = hf_hub_download(
        repo_id=HF_DATASET_REPO,
        filename=filename,
        repo_type="dataset",
        token=HF_TOKEN,
    )
    payload = json.loads(Path(local).read_text(encoding="utf-8"))
    key = (payload["evaluator"], payload["caseId"])
    prev = latest.get(key)
    if prev is None or payload.get("answeredAt", "") >= prev.get("answeredAt", ""):
        latest[key] = payload

out_dir = Path(__file__).resolve().parents[1] / "data" / "labels_slope_level"
out_dir.mkdir(parents=True, exist_ok=True)
out_path = out_dir / "hf_responses.csv"

with out_path.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["evaluator", "caseId", "choice", "answeredAt"],
    )
    writer.writeheader()
    for row in sorted(latest.values(), key=lambda x: (x["evaluator"], x["caseId"])):
        writer.writerow(
            {
                "evaluator": row["evaluator"],
                "caseId": row["caseId"],
                "choice": row["choice"],
                "answeredAt": row.get("answeredAt", ""),
            }
        )

print(f"{len(latest)} labels -> {out_path}")
