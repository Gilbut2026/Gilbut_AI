from __future__ import annotations

import os
from pathlib import Path

from huggingface_hub import HfApi

HERE = Path(__file__).resolve().parent
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
HF_SPACE_REPO = os.getenv("HF_SPACE_REPO", "").strip()
HF_DATASET_REPO = os.getenv("HF_DATASET_REPO", "").strip()

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN 환경변수가 필요합니다.")
if not HF_SPACE_REPO:
    raise RuntimeError("HF_SPACE_REPO 환경변수가 필요합니다.")
if not HF_DATASET_REPO:
    raise RuntimeError("HF_DATASET_REPO 환경변수가 필요합니다.")

api = HfApi(token=HF_TOKEN)

api.create_repo(
    repo_id=HF_DATASET_REPO,
    repo_type="dataset",
    private=True,
    exist_ok=True,
    token=HF_TOKEN,
)
api.create_repo(
    repo_id=HF_SPACE_REPO,
    repo_type="space",
    space_sdk="gradio",
    private=False,
    exist_ok=True,
    token=HF_TOKEN,
)

api.upload_folder(
    repo_id=HF_SPACE_REPO,
    repo_type="space",
    folder_path=str(HERE),
    ignore_patterns=[
        "__pycache__/*",
        "deploy_space.py",
        "export_labels.py",
    ],
    token=HF_TOKEN,
    commit_message="Deploy Gilbut slope preference survey",
)

print(f"Space uploaded: {HF_SPACE_REPO}")
print(f"Dataset ready: {HF_DATASET_REPO}")
print("Space Settings에서 HF_TOKEN Secret과 HF_DATASET_REPO Variable을 설정하세요.")
