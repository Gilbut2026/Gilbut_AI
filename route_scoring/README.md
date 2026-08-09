# 경로 접근성 스코어링

거동이 불편한 사용자를 위해 Backend가 만든 경로 후보를 Hard Filter하고, 접근성 점수를 계산해 순위와 DRT/콜택시 안내 판단을 반환한다.

## 처리 흐름

```text
Backend 경로 후보
      ↓
Hard Filter
      ↓
점수 계산
      ↓
동점 기준 정렬
      ↓
DRT / 콜택시 판단
```

Score Function의 외부 입력 명칭과 enum은 Backend 온보딩 계약을 그대로 사용한다. 별도의 `walkingTolerance`, `stairAbility`, `transferAbility`, `assistiveDevice`, `candidateId` 변환 계층을 두지 않는다.

## Backend 기준 사용자 입력

```json
{
  "userContext": {
    "walkingDuration": "WITHIN_20_MINUTES",
    "stairLevel": "SLIGHTLY_DIFFICULT",
    "restStopPreference": "REQUIRED",
    "transferLevel": "FEWER_PREFERRED",
    "mobilityAid": "CANE_OR_WALKER"
  }
}
```

허용값:

| 필드 | 값 |
|---|---|
| `walkingDuration` | `UNABLE_TO_WALK`, `WITHIN_10_MINUTES`, `WITHIN_20_MINUTES`, `OVER_30_MINUTES` |
| `stairLevel` | `AVAILABLE`, `SLIGHTLY_DIFFICULT`, `DIFFICULT` |
| `restStopPreference` | `REQUIRED`, `NO_PREFERENCE` |
| `transferLevel` | `AVAILABLE`, `FEWER_PREFERRED`, `AVOID_PREFERRED` |
| `mobilityAid` | `NOT_USED`, `CANE_OR_WALKER`, `WHEELCHAIR` |

`todayConditionImpact`는 현재 온보딩에서 제거되어 Score Function 입력에서도 사용하지 않는다.

`restStopPreference`는 Backend 계약과 맞추기 위해 입력에 포함되지만 accessibility-score-v1 점수 산식에는 아직 반영하지 않는다.

## 경로 후보 입력

Backend 기준 식별자는 `routeId`이다.

```json
{
  "routeId": "route-001",
  "routeType": "TRANSIT",
  "routeOption": null,
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
        "stair": { "state": "PRESENT", "count": 1 },
        "overpass": { "state": "ABSENT", "count": 0 },
        "underpass": { "state": "UNKNOWN", "count": null }
      }
    }
  ]
}
```

현재 점수 계산의 필수 metrics는 다음 세 개다.

- `totalWalkTimeSec`
- `totalWalkDistanceM`
- `transferCount`

`totalTimeSec`, `routeType`, `routeOption`은 Backend 계약에 포함되지만 현재 점수 산식에는 직접 사용하지 않는다.

## 장애물 상태

| state | 의미 |
|---|---|
| `PRESENT` | 조회 성공 + 장애물 확인 |
| `ABSENT` | 조회 성공 + 장애물 없음 |
| `UNKNOWN` | 조회 실패 등으로 확인 불가 |

`UNKNOWN`은 장애물이 없다는 뜻으로 처리하지 않는다. 같은 도보 구간에서 여러 장애물 상태가 모두 `UNKNOWN`이어도 구간 단위로 한 번만 감점한다.

## 날씨

Backend 요청 계약과 별개로 AI Server가 기상청 조회 결과를 `environment`로 보강해 Score Function에 전달한다.

```json
{
  "environment": {
    "weatherCondition": "CLEAR",
    "weatherLookupStatus": "SUCCESS"
  }
}
```

지원 날씨:

`CLEAR`, `RAIN`, `HEAVY_RAIN`, `SNOW`, `HEAVY_SNOW`, `HEAT`, `SEVERE_HEAT`, `COLD`, `SEVERE_COLD`

날씨 조회 실패 시 `weatherLookupStatus != SUCCESS`이면 날씨 페널티를 적용하지 않는다.

## Hard Filter

### 보행 불가

`walkingDuration = UNABLE_TO_WALK`이면 일반 경로 후보를 모두 `FILTERED` 처리한다. 통과 경로가 0개가 되면 기존 DRT 판단에서 `NO_PASSABLE_ROUTE`로 연결된다.

### 계단

- `stairLevel = AVAILABLE`: 계단 허용, 작은 감점
- `stairLevel = SLIGHTLY_DIFFICULT`: 계단 허용, 큰 감점
- `stairLevel = DIFFICULT`: 확인된 외부 계단이 있는 경로 Hard Filter
- `mobilityAid = WHEELCHAIR`: 외부 계단이 있거나 장애물 조회가 `UNKNOWN`이면 Hard Filter

FilterCode:

- `STAIR_DIFFICULT_WITH_EXTERNAL_STAIR`
- `WHEELCHAIR_WITH_EXTERNAL_STAIR`
- `WALK_TIME_EXCEEDED`

`UNABLE_TO_WALK`도 현재 Backend에 이미 존재하는 `WALK_TIME_EXCEEDED` 코드로 반환한다.

## 점수

```text
score = -(
  walkTimePenalty
  + walkDistancePenalty
  + obstaclePenalty
  + transferPenalty
  + weatherPenalty
)
```

0에 가까울수록 더 편한 경로다.

Backend 온보딩 값에 따른 현재 가중치:

- 보행: `WITHIN_10_MINUTES` > `WITHIN_20_MINUTES` > `OVER_30_MINUTES` 순으로 도보 부담 가중
- 계단: `SLIGHTLY_DIFFICULT`이면 장애물 감점을 크게 적용
- 환승: `AVAILABLE` < `FEWER_PREFERRED` < `AVOID_PREFERRED` 순으로 환승 감점 증가
- 보조기구: `NOT_USED`가 아니면 장애물 감점 증폭

가중치는 실제 경로 데이터 검증 후 조정할 예정이며 `scoring/policy.py`에서 관리한다.

## 동점 처리

점수가 같으면 다음 순서로 정한다.

1. 장애물 weight가 적은 경로
2. 도보시간이 짧은 경로
3. 환승 횟수가 적은 경로
4. `providerRank`가 높은 경로

## DRT / 콜택시 판단

현재 DRT 판단 기준:

- 통과 가능한 일반 경로가 없음
- 보조기구 사용
- 최상위 경로 도보거리 800m 이상
- 최상위 경로 환승 2회 이상
- 폭우, 폭설, 폭염, 한파

휠체어 사용자는 수원 똑버스 정책상 DRT 대신 교통약자 콜택시 안내로 분기한다.

응답 예시:

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

DRT reasonCodes:

- `ASSISTIVE_DEVICE`
- `LONG_WALK_DISTANCE`
- `MANY_TRANSFERS`
- `SEVERE_WEATHER`
- `NO_PASSABLE_ROUTE`

## 오류

입력이 유효하지 않으면 결과 대신 오류를 반환한다.

```json
{
  "requestId": "req-001",
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "duplicate routeId: route-001",
    "retryable": false
  }
}
```

## 코드 구조

```text
route_scoring/
├── scoring/
│   ├── engine.py
│   ├── policy.py
│   ├── validation.py
│   ├── filters.py
│   ├── score.py
│   ├── obstacles.py
│   ├── drt.py
│   └── weather_penalty.py
├── examples/
│   ├── request.json
│   └── run.py
└── docs/
    └── design.md
```

직접 호출:

```python
from scoring import score_routes

result = score_routes(request)
```

FastAPI 연동 시 Backend 요청을 그대로 받고, AI Server 내부에서 날씨 등 AI 소유 정보를 보강한 뒤 `score_routes()`를 호출하는 구조를 사용한다.
