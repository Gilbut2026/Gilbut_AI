from __future__ import annotations

import io
import json
import os
from pathlib import Path

from huggingface_hub import HfApi

HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "").strip()
LOCAL_DIR = Path(os.getenv("LOCAL_RESPONSE_DIR", "/tmp/gilbut_slope_responses"))


class ResponseStore:
    def __init__(self):
        self.api = HfApi(token=HF_TOKEN or None)
        self.remote = bool(HF_TOKEN and HF_DATASET_REPO)
        LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        if self.remote:
            self.api.create_repo(
                repo_id=HF_DATASET_REPO,
                repo_type="dataset",
                private=True,
                exist_ok=True,
                token=HF_TOKEN,
            )

    def _path(self, evaluator: str, case_id: str) -> str:
        return f"responses/{evaluator}/{case_id}.json"

    def completed_case_ids(self, evaluator: str) -> set[str]:
        if self.remote:
            try:
                prefix = f"responses/{evaluator}/"
                files = self.api.list_repo_files(
                    repo_id=HF_DATASET_REPO,
                    repo_type="dataset",
                    token=HF_TOKEN,
                )
                return {
                    Path(path).stem
                    for path in files
                    if path.startswith(prefix) and path.endswith(".json")
                }
            except Exception:
                return set()

        folder = LOCAL_DIR / evaluator
        if not folder.exists():
            return set()
        return {path.stem for path in folder.glob("*.json")}

    def save_response(self, payload: dict) -> None:
        evaluator = str(payload["evaluator"])
        case_id = str(payload["caseId"])
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        if self.remote:
            self.api.upload_file(
                path_or_fileobj=io.BytesIO(body),
                path_in_repo=self._path(evaluator, case_id),
                repo_id=HF_DATASET_REPO,
                repo_type="dataset",
                token=HF_TOKEN,
                commit_message=f"Save {evaluator} {case_id}",
            )
            return

        folder = LOCAL_DIR / evaluator
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{case_id}.json").write_bytes(body)
