# FastAPI Route Scoring Server

`Gilbut_AI`의 Backend 연동용 HTTP 서버 계층입니다.

실제 접근성 점수 계산, Hard Filter, 장애물 집계, Ranking, DRT/콜택시 판단은 `route_scoring/`이 담당하고, `api/`는 요청 수신·날씨 조회·Score Function 호출·응답 반환을 담당합니다.

## 현재 구현 상태

### 구현 완료

- [x] `GET /health` 서버 상태 확인
- [x] `POST /routes/score` 경로 스코어링 API
- [x] Backend의 `userContext` / `candidates` / `walkSegments` 수신
- [x] route별 `walkSegments` 내 계단·육교·지하보도 집계
- [x] 사용자 보행 가능 시간 반영
- [x] 계단 이용 수준 및 보조기구 조건 반영
- [x] 환승 횟수 및 환승 선호도 반영
- [x] Hard Filter 적용
- [x] route별 Score 계산 및 Ranking
- [x] 기상청 API 조회 및 날씨 penalty 반영
- [x] DRT / 콜택시 안내 판단
- [x] Backend 응답 계약 형식 반환

### 미구현 / 추가 필요

- [ ] 오르막길·경사도 정보 반영 X
- [ ] LLM 연동 X
- [ ] STT(Speech-to-Text) 연동 X
- [ ] TTS(Text-to-Speech) 연동 X

> 현재 FastAPI는 **경로 접근성 스코어링 서버** 범위까지만 구현되어 있습니다. 오르막길/경사도와 LLM·STT·TTS 관련 기능은 현재 구현 범위에 포함되어 있지 않습니다.

## 전체 구조

```text
Gilbut_AI/
├── api/                       FastAPI 서버 계층
│   ├── __init__.py
│   ├── app.py                 FastAPI 진입점
│   ├── README.md              연동 문서
│   ├── requirements.txt       서버 의존성
│   └── .env.example           기상청 API 키 예시
│
└── route_scoring/             기존 Score Function
    ├── scoring/
    ├── examples/
    └── docs/
```

## 처리 흐름

```text
Backend
   │
   │ POST /routes/score
   │ userContext + candidates(+ walkSegments)
   ▼
api/app.py
   │
   ├─ Backend request 수신
   ├─ 기상청 현재 날씨 조회
   ├─ AI 내부 environment 생성
   ▼
route_scoring.scoring.score_routes()
   │
   ├─ 입력 검증
   ├─ route별 walkSegments 장애물 집계
   ├─ Hard Filter
   ├─ Score 계산
   ├─ Ranking
   └─ DRT / 콜택시 판단
   ▼
Backend 응답 계약 반환
```

FastAPI가 기상청 API를 조회한 뒤 AI 내부에서 생성하여 `score_routes()`에 추가합니다.

전달받은 경로 후보를 그대로 Score Function에 넘기고, 날씨 정보만 AI 내부에서 추가합니다.

## 실행

레포지토리 루트(`Gilbut_AI/`)에서 실행합니다.

```bash
pip install -r api/requirements.txt
cp api/.env.example api/.env
```

`api/.env`에 기상청 서비스 키를 설정합니다.

```env
KMA_SERVICE_KEY=your_kma_service_key_here
```

서버 실행:

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

## Endpoint

### `GET /health`

```json
{
  "status": "ok"
}
```

### `POST /routes/score`

Backend가 생성한 경로 후보를 받아 접근성 점수와 순위를 계산합니다.

후보 개수는 고정하지 않습니다. Backend가 `candidates`에 전달한 모든 route를 평가합니다.

## Backend 요청 구조

Backend → FastAPI 요청에는 `environment`가 포함되지 않습니다.

```json
{
  "requestId": "req-001",
  "userContext": {
    "walkingDuration": "WITHIN_20_MINUTES",
    "stairLevel": "SLIGHTLY_DIFFICULT",
    "restStopPreference": "REQUIRED",
    "transferLevel": "FEWER_PREFERRED",
    "mobilityAid": "CANE_OR_WALKER"
  },
  "candidates": [
    {
      "routeId": "route-001",
      "providerRank": 1,
      "metrics": {
        "totalTimeSec": 1200,
        "totalWalkTimeSec": 600,
        "totalWalkDistanceM": 700,
        "transferCount": 1
      },
      "walkSegments": [
        {
          "segmentScope": "EXTERNAL_WALK",
          "accessibilitySignals": {
            "stair": {"state": "PRESENT", "count": 1},
            "overpass": {"state": "ABSENT", "count": 0},
            "underpass": {"state": "PRESENT", "count": 1}
          }
        }
      ]
    }
  ]
}
```

## `walkSegments` 처리

`walkSegments`는 별도의 route 후보가 아니라 **각 route 안에 포함된 도보 이동 구간 목록**입니다.

예를 들어 한 route에 도보 구간이 여러 개 있으면:

```text
route-001
├─ walkSegment-1: stair 1, overpass 0, underpass 0
├─ walkSegment-2: stair 2, overpass 1, underpass 0
└─ walkSegment-3: stair 0, overpass 0, underpass 1
```

`route_scoring/scoring/obstacles.py`가 해당 route의 모든 `walkSegments`를 순회해 장애물을 route 단위로 누적합니다.

```text
stair     = 3
overpass  = 1
underpass = 1
```

현재 종류별 obstacle weight:

```text
stair     × 1.0
overpass  × 0.7
underpass × 0.7
```

위 예시의 route obstacle weight:

```text
3 × 1.0 + 1 × 0.7 + 1 × 0.7 = 4.4
```

이 값에 사용자의 `stairLevel`과 `mobilityAid` 조건을 반영해 `obstaclePenalty`를 계산하고 최종 route score에 포함합니다.

```text
각 route
   ↓
모든 walkSegments 순회
   ↓
계단 / 육교 / 지하보도 누적
   ↓
route obstacle weight
   ↓
stairLevel / mobilityAid 반영
   ↓
obstaclePenalty
   ↓
최종 route score
```

`UNKNOWN`은 장애물이 없다는 뜻이 아닙니다. 같은 도보 구간에서 여러 신호가 `UNKNOWN`이어도 해당 구간의 UNKNOWN penalty는 한 번만 적용합니다.

> FastAPI가 Transit API의 WALK 구간을 직접 재조회해 `walkSegments`를 생성하는 것은 아닙니다. 현재 `walkSegments`는 Backend가 요청에 포함해 전달하고 FastAPI는 그대로 Score Function으로 전달합니다.

## 날씨 처리

날씨는 **Backend 입력이 아니라 AI FastAPI가 담당**합니다.

`api/app.py`가 요청을 받으면 `route_scoring/scoring/weather_penalty.py`의 `get_weather_environment()`를 호출하여 내부 `environment`를 생성합니다.

성공:

```json
{
  "weatherCondition": "RAIN",
  "weatherLookupStatus": "SUCCESS"
}
```

실패:

```json
{
  "weatherCondition": "CLEAR",
  "weatherLookupStatus": "FAILED"
}
```

`FAILED`의 `CLEAR`는 실제 날씨가 맑다는 뜻이 아니라, 조회 실패 시 날씨 penalty를 적용하지 않기 위한 fallback입니다.

## Score Function 연결

FastAPI에는 Score 계산식을 복제하지 않습니다.

```python
scoring_request = deepcopy(request)
scoring_request["environment"] = get_weather_environment()
return score_routes(scoring_request)
```

여기서 `request`는 Backend가 보낸 요청이고, `environment`는 FastAPI가 AI 내부에서 새로 만든 값입니다.

점수 정책은 `route_scoring/scoring/`에서만 관리합니다.

## 응답

```json
{
  "requestId": "req-001",
  "scoringVersion": "accessibility-score-v1",
  "results": [
    {
      "routeId": "route-001",
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
    "basedOnRouteId": "route-001"
  }
}
```

## 오류 처리

Score Function 입력 검증 오류는 기존 `VALIDATION_ERROR` 형식을 그대로 반환합니다.

예상하지 못한 서버 내부 오류는 HTTP 500으로 반환합니다.

```json
{
  "requestId": "req-001",
  "error": {
    "code": "INTERNAL_ERROR",
    "message": "route scoring failed",
    "retryable": true
  }
}
```

## Backend 연결

```text
AI_SCORING_URL=http://<AI_SERVER_HOST>:8000/routes/score
```

현재 Backend가 전달해야 하는 핵심 데이터는 사용자 온보딩 값, route별 metrics, 그리고 장애물 계산에 필요한 `walkSegments`입니다.

날씨 `environment`는 Backend 전달 항목이 아니라 FastAPI 내부 생성값입니다.
