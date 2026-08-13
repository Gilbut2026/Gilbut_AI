#!/bin/bash
set -e

HERE="$(cd "$(dirname "$0")" && pwd)"
VENV="$HERE/.labelstudio-venv"
TASKS="$HERE/label_studio_tasks.json"
CONFIG="$HERE/label_config.xml"

HOST="127.0.0.1"
PORT="8080"
BASE_URL="http://$HOST:$PORT"

LS_USERNAME="gilbut@local"
LS_PASSWORD="gilbut1234"
LS_TOKEN="gilbut-local-token"

echo "========================================"
echo " 길벗 가중치 평가용 Label Studio"
echo "========================================"

if [ ! -d "$VENV" ]; then
  echo "[1/4] Python 3.12 전용 환경 생성..."
  python3.12 -m venv "$VENV"
else
  echo "[1/4] 기존 전용 환경 사용..."
fi

echo "[2/4] Label Studio 설치/확인..."
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q label-studio

echo "[3/4] Label Studio 서버 시작..."
"$VENV/bin/label-studio" start \
  --host "$HOST" \
  --port "$PORT" \
  --username "$LS_USERNAME" \
  --password "$LS_PASSWORD" \
  --user-token "$LS_TOKEN" \
  --enable-legacy-api-token \
  --no-browser \
  > "$HERE/label_studio.log" 2>&1 &

SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" 2>/dev/null; then
    kill "$SERVER_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "    서버 준비 대기 중..."

"$VENV/bin/python" - <<PY
import time
import requests

url = "$BASE_URL"
last_error = None

for _ in range(90):
    try:
        r = requests.get(url, timeout=2, allow_redirects=False)
        if r.status_code < 500:
            print("    서버 준비 완료")
            break
    except Exception as e:
        last_error = e
    time.sleep(1)
else:
    raise SystemExit(
        "Label Studio 서버가 준비되지 않았습니다. "
        "label_studio.log를 확인해주세요. "
        f"마지막 오류: {last_error}"
    )
PY

echo "[4/4] 프로젝트 생성 및 120개 데이터 import..."

"$VENV/bin/python" - <<PY
import json
from pathlib import Path
import requests

BASE_URL = "$BASE_URL"
TOKEN = "$LS_TOKEN"
TITLE = "길벗 경로 가중치 평가"

tasks_path = Path(r"$TASKS")
config_path = Path(r"$CONFIG")

tasks = json.loads(tasks_path.read_text(encoding="utf-8"))
label_config = config_path.read_text(encoding="utf-8")

headers = {
    "Authorization": f"Token {TOKEN}",
    "Content-Type": "application/json",
}

r = requests.get(
    f"{BASE_URL}/api/projects/",
    headers=headers,
    params={"page_size": 100},
    timeout=30,
)
r.raise_for_status()
payload = r.json()

if isinstance(payload, dict):
    projects = payload.get("results", [])
elif isinstance(payload, list):
    projects = payload
else:
    projects = []

project = next((p for p in projects if p.get("title") == TITLE), None)

if project is None:
    r = requests.post(
        f"{BASE_URL}/api/projects/",
        headers=headers,
        json={
            "title": TITLE,
            "description": "경로 A/B human preference 평가를 통한 score function 가중치 튜닝",
            "label_config": label_config,
            "maximum_annotations": 3,
            "show_skip_button": False,
        },
        timeout=30,
    )
    r.raise_for_status()
    project = r.json()
    print(f"    프로젝트 생성 완료 (ID={project['id']})")
else:
    print(f"    기존 프로젝트 사용 (ID={project['id']})")

project_id = project["id"]

r = requests.get(
    f"{BASE_URL}/api/projects/{project_id}/",
    headers=headers,
    timeout=30,
)
r.raise_for_status()
project_detail = r.json()
task_number = int(project_detail.get("task_number") or 0)

if task_number == 0:
    r = requests.post(
        f"{BASE_URL}/api/projects/{project_id}/import",
        headers=headers,
        json=tasks,
        timeout=120,
    )
    r.raise_for_status()
    result = r.json()
    print(f"    데이터 import 완료: {result.get('task_count', len(tasks))}개")
else:
    print(f"    이미 {task_number}개 task가 있어 import를 건너뜁니다.")

print()
print("========================================")
print(" 준비 완료")
print("========================================")
print(f"URL: {BASE_URL}")
print("로그인 ID: gilbut@local")
print("비밀번호: gilbut1234")
print(f"Project ID: {project_id}")
print()
print("종료하려면 이 터미널에서 Ctrl+C")
PY

open "$BASE_URL" >/dev/null 2>&1 || true
wait "$SERVER_PID"