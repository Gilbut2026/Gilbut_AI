# 경로 접근성 스코어링

거동이 불편한 사용자를 위해 경로 후보를 다시 평가합니다. 지도 API가 최단 시간을 우선하는 것과 달리, 사용자가 답한 보행 능력을 기준으로 순위를 매깁니다.

```
경로 후보 → Hard Filter → 점수 계산 → 정렬 → DRT 판단 → 결과
```

## 사용법

```python
from scoring import score_routes

result = score_routes({
    "requestId": "req-001",
    "userContext": {
        "walkingTolerance": "AROUND_20_MIN",
        "stairAbility": "DIFFICULT",
        "transferAbility": "AVAILABLE",
        "assistiveDevice": "CANE",
    },
    "environment": {
        "weatherCondition": "CLEAR",
        "weatherLookupStatus": "SUCCESS",
    },
    "candidates": [...],
})
```

```json
{
  "requestId": "req-001",
  "scoringVersion": "accessibility-score-v1",
  "results": [
    {
      "candidateId": "route-001",
      "status": "SCORED",
      "score": -1.45,
      "rank": 1,
      "filterCodes": [],
      "scoreBreakdown": {
        "walkTimePenalty": 0.0,
        "walkDistancePenalty": 0.45,
        "obstaclePenalty": 0.0,
        "transferPenalty": 1.0,
        "weatherPenalty": 0.0
      }
    }
  ],
  "drtDecision": {
    "show": true,
    "priority": false,
    "taxiGuide": false,
    "reasonCodes": ["ASSISTIVE_DEVICE"],
    "basedOnCandidateId": "route-001"
  }
}
```

전체 예시는 [`examples/`](examples/)에 있습니다.

## 구조

```
scoring/
├── engine.py       진입점 · 검증 → 필터 → 점수 → 정렬 → DRT
├── policy.py       가중치와 상수 · 튜닝은 여기만
├── validation.py   입력 검증
├── filters.py      Hard Filter
├── score.py        점수 계산
├── obstacles.py    계단 · 육교 · 지하보도 집계
└── drt.py          똑버스 · 콜택시 판단
```

## 점수

```
score = −(도보시간 + 도보거리 + 장애물 + 환승 + 날씨)
```

0에 가까울수록 편한 경로입니다. 각 항목의 가중치는 사용자 응답에 따라 달라집니다.

| 항목 | 가중치 결정 |
|:--|:--|
| 도보시간 · 거리 | 한 번에 걸을 수 있는 시간 |
| 장애물 | 계단 이용 가능 수준 × 보조기구 사용 여부 |
| 환승 | 환승 가능 수준 |
| 날씨 | 날씨 등급 × 도보거리 |

연속값은 구간별 계단식으로 정규화합니다. 구간 경계는 온보딩 선택지와 일치시켰습니다.

```
도보시간     ~10분  0.0    ~20분  0.3    ~30분  0.8    30분+  1.0
도보거리    ~400m  0.0   ~800m  0.3   ~1200m  0.8   1200m+  1.0
```

설계 배경과 판단 근거는 [`docs/design.md`](docs/design.md)를 참고하세요.

## 테스트

```bash
python tests/test_scoring.py
```

## 한계

- 가중치가 임의값입니다. 실제 경로 데이터로 검증 후 조정할 예정입니다.
- 선형 가중합이라 요소 간 상호작용을 반영하지 못합니다.
- 육교·지하보도의 접근 가능 여부(엘리베이터·경사로)를 구분할 수 없어 가중치로 근사합니다.
- 경사와 오르막은 반영하지 않습니다. 지도 API가 도보 경로 수준의 데이터를 제공하지 않습니다.
