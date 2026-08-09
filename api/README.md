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

FastAPI가 기상청 API를 조회한 뒤 AI 내부 `environment`를 생성하여 `score_routes()`에 추가합니다.

Backend에서 전달받은 경로 후보는 그대로 Score Function에 전달하며, 날씨 정보만 AI 내부에서 추가합니다.

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

FastAPI는 **각 경로 후보의 스코어링 결과와 DRT/콜택시 안내 판단을 Backend에 반환**합니다.

정상 응답의 최상위 필드는 다음 4개입니다.

| 필드 | 의미 |
| --- | --- |
| `requestId` | Backend가 보낸 요청 ID. 요청과 응답을 매칭하는 데 사용 |
| `scoringVersion` | 현재 적용된 경로 스코어링 정책 버전 |
| `results` | Backend가 보낸 각 route의 스코어링 또는 필터링 결과 |
| `drtDecision` | 일반 경로와 별도로 DRT 또는 교통약자 콜택시를 안내할지에 대한 판단 |

### 정상 응답 예시

아래 예시는 `route-001`은 정상적으로 점수가 계산되고, `route-002`는 계단 때문에 제외된 경우입니다.

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
    },
    {
      "routeId": "route-002",
      "status": "FILTERED",
      "score": null,
      "rank": null,
      "filterCodes": ["STAIR_DIFFICULT_WITH_EXTERNAL_STAIR"]
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

### `results`

`results`에는 Backend가 전달한 각 `routeId`에 대한 결과가 들어갑니다.

| 필드 | 의미 |
| --- | --- |
| `routeId` | Backend 요청의 route와 결과를 연결하는 식별자 |
| `status` | `SCORED` 또는 `FILTERED` |
| `score` | 최종 접근성 점수. `FILTERED`이면 `null` |
| `rank` | 통과한 경로 중 추천 순위. 1이 가장 우선이며 `FILTERED`이면 `null` |
| `filterCodes` | 해당 경로가 Hard Filter로 제외된 이유. 통과한 경우 빈 배열 |
| `scoreBreakdown` | 최종 점수를 구성하는 항목별 penalty. `SCORED` 경로에만 포함 |

#### `status = SCORED`

Hard Filter를 통과하여 실제 점수와 순위가 계산된 경로입니다.

```text
status = SCORED
→ score 존재
→ rank 존재
→ filterCodes = []
→ scoreBreakdown 존재
```

현재 score는 다음 penalty의 합에 음수를 붙여 계산합니다.

```text
score = -(도보시간 + 도보거리 + 장애물 + 환승 + 날씨 penalty)
```

따라서 **score가 클수록, 즉 0에 가까울수록 더 편한 경로**입니다.

예를 들어:

```text
route-A score = -1.45
route-B score = -5.20

→ route-A가 더 우선되는 경로
```

#### `status = FILTERED`

점수를 매기기 전에 통행이 어렵거나 불가능하다고 판단되어 제외된 경로입니다.

```text
status = FILTERED
→ score = null
→ rank = null
→ filterCodes에 제외 사유 포함
→ scoreBreakdown 없음
```

현재 `filterCodes`는 다음과 같습니다.

| 코드 | 의미 |
| --- | --- |
| `STAIR_DIFFICULT_WITH_EXTERNAL_STAIR` | 계단 이용이 어려운 사용자에게 확인된 외부 계단이 존재 |
| `WHEELCHAIR_WITH_EXTERNAL_STAIR` | 휠체어 사용 경로에 외부 계단이 있거나 장애물 정보가 불확실함 |
| `WALK_TIME_EXCEEDED` | 보행 불가 사용자이거나 설정된 보행 가능 시간을 크게 초과 |

### `scoreBreakdown`

최종 `score`가 왜 해당 값이 되었는지 Backend에서 확인할 수 있도록 항목별 penalty를 반환합니다.

| 필드 | 의미 |
| --- | --- |
| `walkTimePenalty` | 전체 도보시간과 사용자의 보행 가능 시간을 반영한 감점 |
| `walkDistancePenalty` | 전체 도보거리와 사용자의 보행 가능 수준을 반영한 감점 |
| `obstaclePenalty` | route 내부의 계단·육교·지하보도와 계단 이용 수준·보조기구를 반영한 감점 |
| `transferPenalty` | 환승 횟수와 사용자의 환승 선호도를 반영한 감점 |
| `weatherPenalty` | AI FastAPI가 조회한 날씨와 도보거리를 반영한 감점 |

Backend에서는 `scoreBreakdown`을 이용해 단순 순위뿐 아니라 추천 근거를 구성할 수 있습니다.

예:

```text
route-001
→ 도보시간 감점이 작음
→ 장애물 감점이 없음
→ 환승 1회 감점 존재
→ 최종 1위
```

### `drtDecision`

`drtDecision`은 특정 route의 점수가 아니라 **일반 대중교통 경로 결과를 바탕으로 DRT 또는 교통약자 콜택시를 추가 안내할지 판단한 결과**입니다.

| 필드 | 의미 |
| --- | --- |
| `show` | DRT 안내를 사용자에게 보여줄지 여부 |
| `priority` | DRT를 일반 경로보다 우선적으로 노출할지 여부 |
| `taxiGuide` | DRT 대신 교통약자 콜택시 안내가 필요한지 여부 |
| `reasonCodes` | DRT/콜택시 판단 근거 목록 |
| `basedOnRouteId` | DRT 판단의 기준이 된 현재 최상위 통과 경로 ID. 통과 경로가 없으면 `null` 가능 |

현재 `reasonCodes`는 다음과 같습니다.

| 코드 | 의미 |
| --- | --- |
| `ASSISTIVE_DEVICE` | 지팡이·보행기·휠체어 등 보조기구 사용 조건 |
| `LONG_WALK_DISTANCE` | 최상위 일반 경로의 도보거리가 길어 DRT 우선 안내 필요 |
| `MANY_TRANSFERS` | 최상위 일반 경로의 환승 횟수가 많아 DRT 우선 안내 필요 |
| `SEVERE_WEATHER` | 폭우·폭설·폭염·한파 등 이동 부담이 큰 날씨 |
| `NO_PASSABLE_ROUTE` | Hard Filter 이후 통행 가능한 일반 경로가 없음 |

예를 들어:

```json
{
  "show": true,
  "priority": true,
  "taxiGuide": false,
  "reasonCodes": ["LONG_WALK_DISTANCE", "MANY_TRANSFERS"],
  "basedOnRouteId": "route-003"
}
```

이면 Backend에서는 **일반 경로는 존재하지만 도보거리와 환승 부담이 커서 DRT를 우선적으로 함께 안내해야 한다**고 해석하면 됩니다.

휠체어 사용자는 현재 정책상 DRT 대신 교통약자 콜택시 안내 대상으로 처리되므로 `taxiGuide=true`가 반환될 수 있습니다.

### Backend에서 응답을 사용하는 흐름

```text
FastAPI 응답
   ↓
results에서 FILTERED 경로 제외
   ↓
SCORED 경로의 rank 기준으로 경로 카드 정렬
   ↓
scoreBreakdown을 추천 근거/설명에 활용 가능
   ↓
drtDecision.show / priority / taxiGuide 확인
   ↓
DRT 또는 콜택시 UI 노출 여부 결정
```

즉 Backend가 FastAPI에서 받는 핵심 결과는 **각 route의 통과 여부, 접근성 점수, 추천 순위, 제외 사유, 항목별 감점 근거, DRT/콜택시 안내 판단**입니다.

## 오류 처리

Score Function 입력 검증 오류는 `VALIDATION_ERROR` 형식으로 반환합니다.

```json
{
  "requestId": "req-001",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "totalWalkTimeSec is required: route-001",
    "retryable": false
  }
}
```

예상하지 못한 FastAPI 내부 오류는 HTTP 500으로 반환합니다.

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
