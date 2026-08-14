---
title: Gilbut Slope Preference Evaluation
emoji: 🛣️
colorFrom: blue
colorTo: green
sdk: gradio
app_file: app.py
pinned: false
---

# Gilbut Slope Preference Evaluation

`Gilbut_AI`의 `slopeLevel` 기반 route-scoring weight tuning을 위한 Hugging Face Space UI입니다.

## 무엇을 평가하나

각 문항에는 사용자 프로필과 두 경로가 표시됩니다.

- walkingDuration
- stairLevel
- **slopeLevel**
- transferLevel
- mobilityAid
- weather
- 각 경로의 도보 시간/거리, 환승, 장애물, 최대 오르막/내리막 경사

평가자는 화면에 제시된 사용자의 입장에서 `왼쪽 경로 / 비슷함 / 오른쪽 경로`를 선택합니다.

## 편향 방지

- 좌/우 경로는 `평가자 슬롯 + caseId` 기준으로 결정적으로 랜덤화됩니다.
- 저장할 때는 다시 원래 `A / B / SIMILAR` 라벨로 변환합니다.
- 기본 설정은 9명의 평가자가 120 case를 3명씩 평가하도록 배정합니다.
  - 평가자 1명당 40문항
  - case 1개당 3개 독립 평가

## 빠른 배포

토큰을 채팅이나 코드에 적지 말고 로컬 환경변수로만 설정합니다.

```bash
cd route_scoring/tuning/hf_space
pip install -r requirements.txt

export HF_TOKEN=hf_...
export HF_SPACE_REPO=Robot-HJM/gilbut-slope-preference-survey
export HF_DATASET_REPO=Robot-HJM/gilbut-slope-preference-data

python deploy_space.py
```

`deploy_space.py`는 Space와 private Dataset repo 생성을 시도한 뒤 UI 파일을 Space에 업로드합니다.

그 다음 Hugging Face Space `Settings -> Variables and secrets`에서 아래를 추가합니다.

### Secret
- `HF_TOKEN`: Dataset repo에 write 가능한 Hugging Face token

### Variable
- `HF_DATASET_REPO`: 예) `Robot-HJM/gilbut-slope-preference-data`

> 토큰은 GitHub에 커밋하거나 채팅에 붙여넣지 않습니다.

## 저장 형식

각 응답은 Dataset repo에 독립 파일로 저장됩니다.

```text
responses/
  slot_01/
    slv2_case_001.json
    ...
```

동일 평가자가 동일 case를 다시 제출하면 같은 경로에 최신 응답이 저장됩니다.
같은 슬롯으로 다시 접속하면 이미 저장된 case는 자동으로 건너뜁니다.

## 튜닝용 CSV 내보내기

로컬에서:

```bash
export HF_TOKEN=...
export HF_DATASET_REPO=Robot-HJM/gilbut-slope-preference-data
python export_labels.py
```

그러면:

```text
route_scoring/tuning/data/labels_slope_level/hf_responses.csv
```

가 생성됩니다. 이 CSV 컬럼은 기존 optimizer가 읽는 형식과 동일합니다.

```text
evaluator,caseId,choice,answeredAt
```

이후:

```bash
cd route_scoring/tuning
python run_tuning.py
```

으로 slopeLevel-aware tuning을 실행합니다.

## 주의

이 평가는 controlled synthetic trade-off case 기반 prototype calibration입니다. 실제 노인/교통약자 사용자 선호를 검증한 결과로 표현하면 안 됩니다.
