# FastAPI Route Scoring Server

Backend와 Route Scoring 모듈을 연결하는 HTTP 서버 계층입니다. 실제 경로 점수 계산, Hard Filter, 장애물 집계, DRT/콜택시 판단은 기존 `scoring/` 모듈이 담당하고 `api/`는 요청 수신과 날씨 보강, 응답 반환만 담당합니다.

## 역할

```text
Backend
   │
   │ POST /routes/score
   ▼
api/app.py
   │
   ├─ Backend request 수신
   ├─ 기상청 현재 날씨 조회
   ├─ environment 보강
   ▼
scoring.score_routes()
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

FastAPI는 Backend가 전달한 `candidates`나 `walkSegments`를 별도로 가공하거나 재생성하지 않습니다. Backend 요청을 그대로 Score Function으로 넘기고 AI가 소유하는 날씨 정보만 추가합니다.

## 디렉토리

```text
route_scoring/
├── api/
│   ├── __init__.py
│   ├── app.py          FastAPI 진입점
│   └── README.md       FastAPI 연동 설명
├── scoring/            기존 Score Function
├── examples/
├── docs/
├── requirements.txt
└── .env.example
```

## 실행

`route_scoring` 디렉토리에서 실행합니다.

```bash
pip install -r requirements.txt
cp .env.example .env
```

`.env`에 기상청 서비스 키를 설정합니다.

```env
KMA_SERVICE_KEY=your_kma_service_key_here
```

서버 실행:

```bash
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

## Endpoint

### `GET /health`

서버 프로세스 상태를 확인합니다.

```json
{
  "status": "ok"
}
```

### `POST /routes/score`

Backend가 생성한 경로 후보를 받아 접근성 점수를 계산합니다.

입력 개수는 고정하지 않습니다. Backend가 `candidates`에 보낸 모든 route를 Score Function에서 평가합니다.

## 요청 구조

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

Backend가 `environment`를 보내더라도 FastAPI에서는 AI Server가 조회한 최신 기상청 값으로 덮어씁니다.

## `walkSegments` 처리

`walkSegments`는 별도의 route 후보가 아니라 **각 route 안에 포함된 도보 이동 구간들**입니다.

예를 들어 한 route에 도보 구간이 여러 개 있으면:

```text
route-001
├─ walkSegment-1: stair 1, overpass 0, underpass 0
├─ walkSegment-2: stair 2, overpass 1, underpass 0
└─ walkSegment-3: stair 0, overpass 0, underpass 1
```

`scoring/obstacles.py`가 해당 route의 모든 `walkSegments`를 순회해 장애물을 누적합니다.

```text
stair     = 3
overpass  = 1
underpass = 1
```

현재 장애물 종류별 weight는 다음과 같습니다.

```text
stair     × 1.0
overpass  × 0.7
underpass × 0.7
```

따라서 위 예시의 route obstacle weight는:

```text
3 × 1.0 + 1 × 0.7 + 1 × 0.7 = 4.4
```

이 값에 사용자의 `stairLevel`과 `mobilityAid` 조건을 반영해 `obstaclePenalty`를 계산하고 최종 route score에 포함합니다.

전체 흐름:

```text
각 route
   ↓
모든 walkSegments 순회
   ↓
계단 / 육교 / 지하보도 누적
   ↓
route obstacle weight 계산
   ↓
사용자 계단 수준 / 보조기구 반영
   ↓
obstaclePenalty
   ↓
최종 route score
```

`UNKNOWN` 상태는 장애물이 없다는 뜻으로 처리하지 않습니다. 같은 도보 구간에서 여러 신호가 `UNKNOWN`이어도 해당 구간에 대한 UNKNOWN penalty는 한 번만 적용합니다.

> 현재 FastAPI가 Transit API의 WALK 구간을 직접 재조회해 `walkSegments`를 생성하는 것은 아닙니다. `walkSegments`는 Backend가 요청에 포함해 전달하는 값이며, FastAPI는 이를 그대로 Score Function으로 전달합니다.

## 날씨 처리

FastAPI가 요청을 받으면 `scoring/weather_penalty.py`의 `get_weather_environment()`를 호출합니다.

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

`FAILED`의 `CLEAR`는 실제 날씨가 맑다는 의미가 아니라 조회 실패 시 날씨 penalty를 적용하지 않기 위한 fallback 값입니다.

## Score Function 연결

FastAPI 자체에는 Score 계산식을 복제하지 않습니다.

```python
scoring_request["environment"] = get_weather_environment()
return score_routes(scoring_request)
```

따라서 점수 정책을 변경할 때는 기존 `scoring/` 모듈만 수정하면 되고 HTTP 서버 로직은 그대로 유지할 수 있습니다.

## 응답

Score Function 응답을 Backend 계약 그대로 반환합니다.

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

FastAPI 내부에서 예상하지 못한 예외가 발생하면 HTTP 500과 함께 다음 형식을 반환합니다.

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

Backend의 AI scoring URL은 실행 중인 FastAPI endpoint 전체 주소로 설정합니다.

```text
AI_SCORING_URL=http://<AI_SERVER_HOST>:8000/routes/score
```

현재 연동에서 Backend가 반드시 전달해야 하는 핵심 데이터는 사용자 온보딩 값, route별 metrics, 그리고 장애물 점수 계산에 필요한 `walkSegments`입니다.
