<div align="center">
<br>
<br>

# 경로 접근성 스코어링

**가장 빠른 길이 아니라, 가장 편한 길**

거동이 불편한 사용자를 위해 경로 후보를 다시 평가합니다

<br>

<img src="https://img.shields.io/badge/python-3.7+-3776AB?style=flat-square&logo=python&logoColor=white" alt="python">
<img src="https://img.shields.io/badge/dependencies-none-success?style=flat-square" alt="dependencies">
<img src="https://img.shields.io/badge/tests-34%20passed-success?style=flat-square" alt="tests">

<br>
<br>

</div>

```
     경로 후보          Hard Filter         점수 계산           정렬            DRT 판단
        │                    │                  │                │                 │
        └───────────────────▶└─────────────────▶└───────────────▶└────────────────▶└──▶ 결과
                       통행 불가 제외      능력 기반 가중합    장애물 우선     똑버스 · 콜택시
```

<div align="center">
<br>

[시작하기](#시작하기) · [요청 형식](#요청-형식) · [응답 형식](#응답-형식) · [구조](#구조) · [점수](#점수) · [한계](#한계)

<br>
</div>

---

<br>

## 시작하기

<table>
<tr><td width="180"><b>Python</b></td><td>3.7 이상 · 표준 라이브러리만 사용</td></tr>
<tr><td><b>외부 패키지</b></td><td>없음</td></tr>
<tr><td><b>개발 · 검증 환경</b></td><td>Python 3.12 · macOS · Linux</td></tr>
</table>

별도 설치 없이 바로 실행됩니다.

<br>

#### 1 · 내려받기

```bash
git clone <레포 주소>
cd score-function
```

#### 2 · 동작 확인

```bash
python tests/test_scoring.py
```

```
34/34 passed
```

#### 3 · 예시 실행

```bash
python examples/run.py
```

`examples/request.json`을 읽어서 스코어링 결과를 출력합니다.

```json
{
  "requestId": "req-001",
  "results": [
    { "routeId": "route-001", "status": "SCORED", "score": -1.45, "rank": 1 },
    { "routeId": "route-003", "status": "SCORED", "score": -4.45, "rank": 2 },
    { "routeId": "route-002", "status": "SCORED", "score": -8.55, "rank": 3 }
  ]
}
```

> [!NOTE]
> 실행은 반드시 레포 최상위 디렉터리(`score-function/`)에서 해야 합니다.
> `scoring` 패키지를 import 하기 때문입니다.

#### 4 · 코드에서 사용하기

```python
from scoring import score_routes

result = score_routes(request)
```

<br>

---

<br>

## 요청 형식

세 부분으로 구성됩니다.

<table>
<tr><td width="180"><code>userContext</code></td><td>사용자가 누구인지 — 온보딩 응답</td></tr>
<tr><td><code>environment</code></td><td>지금 상황이 어떤지 — 날씨</td></tr>
<tr><td><code>candidates</code></td><td>평가할 경로들</td></tr>
</table>

```python
request = {
    "requestId": "req-001",

    # 온보딩에서 받은 사용자 정보
    "userContext": {
        "walkingDuration": "WITHIN_20_MINUTES",       # 한 번에 걸을 수 있는 시간
        "stairLevel": "SLIGHTLY_DIFFICULT",          # 계단 이용 가능 수준
        "restStopPreference": "REQUIRED",             # 이동 중 휴식 필요 여부
        "transferLevel": "AVAILABLE",                 # 환승 선호 수준
        "mobilityAid": "CANE_OR_WALKER",              # 보조기구
    },

    # 현재 환경
    "environment": {
        "weatherCondition": "CLEAR",
        "weatherLookupStatus": "SUCCESS",       # 조회 실패 시 날씨 페널티 미적용
    },

    # 평가할 경로 후보들
    "candidates": [
        {
            "routeId": "route-001",             # 결과와 연결할 식별자
            "providerRank": 1,                  # 지도 API가 준 원래 순서

            "metrics": {
                "totalWalkTimeSec": 600,        # 도보 시간 (초)
                "totalWalkDistanceM": 700,      # 도보 거리 (m)
                "transferCount": 1,             # 환승 횟수
            },

            # 도보 구간별 장애물 정보
            "walkSegments": [
                {
                    "segmentScope": "EXTERNAL_WALK",    # 역 밖 구간
                    "accessibilitySignals": {
                        "stair":     {"state": "PRESENT", "count": 2},
                        "overpass":  {"state": "ABSENT",  "count": 0},
                        "underpass": {"state": "UNKNOWN", "count": None},
                    },
                }
            ],
        },
        # ... 나머지 후보
    ],
}
```

<br>

#### `state` 값의 의미

<table>
<tr><td width="140"><code>PRESENT</code></td><td>조회했고, 장애물이 <b>있음</b></td></tr>
<tr><td><code>ABSENT</code></td><td>조회했고, 장애물이 <b>없음</b></td></tr>
<tr><td><code>UNKNOWN</code></td><td>조회에 <b>실패</b>해서 알 수 없음</td></tr>
</table>

> [!IMPORTANT]
> 장애물이 실제로 없는 것과 확인하지 못한 것은 다르게 처리합니다.

<br>

<details>
<summary><b>사용 가능한 값 전체 보기</b></summary>
<br>

| 필드 | 값 |
|:--|:--|
| `walkingDuration` | `UNABLE_TO_WALK` · `WITHIN_10_MINUTES` · `WITHIN_20_MINUTES` · `OVER_30_MINUTES` |
| `stairLevel` | `AVAILABLE` · `SLIGHTLY_DIFFICULT` · `DIFFICULT` |
| `restStopPreference` | `REQUIRED` · `NO_PREFERENCE` |
| `transferLevel` | `AVAILABLE` · `FEWER_PREFERRED` · `AVOID_PREFERRED` |
| `mobilityAid` | `NOT_USED` · `CANE_OR_WALKER` · `WHEELCHAIR` |
| `weatherCondition` | `CLEAR` · `RAIN` · `HEAVY_RAIN` · `SNOW` · `HEAVY_SNOW` · `HEAT` · `SEVERE_HEAT` · `COLD` · `SEVERE_COLD` |
| `weatherLookupStatus` | `SUCCESS` · `FAILED` · `NOT_REQUESTED` |
| `segmentScope` | `EXTERNAL_WALK` · `STATION_INTERNAL` · `UNKNOWN` |
| `accessibilitySignals.*.state` | `PRESENT` · `ABSENT` · `UNKNOWN` |

</details>

<br>

---

<br>

## 응답 형식

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
      "filterCodes": ["WHEELCHAIR_WITH_EXTERNAL_STAIR"]
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

<br>

<table>
<tr>
<td width="190" valign="top"><code>status</code></td>
<td><code>SCORED</code>는 점수가 매겨진 경로, <code>FILTERED</code>는 통행이 불가능해 제외된 경로입니다.<br>제외된 경로는 <code>score</code>와 <code>rank</code>가 <code>null</code>이고 사유가 <code>filterCodes</code>에 담깁니다.</td>
</tr>
<tr>
<td valign="top"><code>rank</code></td>
<td>1이 가장 편한 경로입니다. <code>results</code> 배열도 이 순서로 정렬되어 있습니다.</td>
</tr>
<tr>
<td valign="top"><code>score</code></td>
<td>0에 가까울수록 편한 경로입니다. 음수이므로 <code>-1.45</code>가 <code>-8.55</code>보다 좋습니다.</td>
</tr>
<tr>
<td valign="top"><code>scoreBreakdown</code></td>
<td>어떤 항목에서 얼마나 감점됐는지입니다.<br>"계단이 없어서 추천했어요" 같은 안내 문구를 만들 때 쓸 수 있습니다.</td>
</tr>
<tr>
<td valign="top"><code>drtDecision</code></td>
<td>똑버스나 콜택시를 안내할지 판단한 결과입니다.</td>
</tr>
</table>

<br>

<details>
<summary><b>결과 코드 전체 보기</b></summary>
<br>

**`filterCodes`** — 경로가 제외된 사유

| 코드 | 뜻 |
|:--|:--|
| `STAIR_DIFFICULT_WITH_EXTERNAL_STAIR` | 계단 이용이 어려운 사용자인데 경로에 계단이 있음 |
| `WHEELCHAIR_WITH_EXTERNAL_STAIR` | 휠체어 사용자인데 계단이 있거나 확인 불가 |
| `WALK_TIME_EXCEEDED` | 보행 불가이거나 걸을 수 있는 시간의 2배를 초과 |

**`drtDecision.reasonCodes`** — DRT 판단 근거

| 코드 | 뜻 |
|:--|:--|
| `ASSISTIVE_DEVICE` | 보조기구 사용자 |
| `LONG_WALK_DISTANCE` | 도보 거리가 800m 이상 |
| `MANY_TRANSFERS` | 환승 2회 이상 |
| `SEVERE_WEATHER` | 폭우 · 폭설 · 폭염 · 한파 |
| `NO_PASSABLE_ROUTE` | 통행 가능한 경로가 없음 |

</details>

<br>

#### 오류

입력이 유효하지 않으면 결과 대신 오류를 반환합니다.

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

> [!WARNING]
> 필수 값이 없는 후보를 0으로 계산하면 오히려 가장 편한 경로로 1위가 됩니다.
> 그래서 계산 전에 검증합니다.

<br>

---

<br>

## 구조

```
score-function
│
├── scoring                 핵심 로직
│   ├── engine.py           진입점 · 검증 → 필터 → 점수 → 정렬 → DRT
│   ├── policy.py           가중치와 상수 · 튜닝은 여기만
│   ├── validation.py       입력 검증
│   ├── filters.py          Hard Filter
│   ├── score.py            점수 계산
│   ├── obstacles.py        계단 · 육교 · 지하보도 집계
│   └── drt.py              똑버스 · 콜택시 판단
│
├── tests
│   └── test_scoring.py     34개 정책 검증
│
├── examples
│   ├── run.py              예시 실행
│   └── request.json        예시 요청
│
└── docs
    └── design.md           설계 배경과 판단 근거
```

<br>

---

<br>

## 점수

<div align="center">
<br>
<br>

### score  =  − ( 도보시간 + 도보거리 + 장애물 + 환승 + 날씨 )

<br>

0에 가까울수록 편한 경로

<br>
<br>
</div>

각 항목의 가중치는 사용자 응답에 따라 달라집니다.

<table>
<tr><td width="200"><b>도보시간 · 거리</b></td><td>한 번에 걸을 수 있는 시간</td></tr>
<tr><td><b>장애물</b></td><td>계단 이용 가능 수준 × 보조기구 사용 여부</td></tr>
<tr><td><b>환승</b></td><td>환승 가능 수준</td></tr>
<tr><td><b>날씨</b></td><td>날씨 등급 × 도보거리</td></tr>
</table>

<br>

연속값은 구간별 계단식으로 정규화합니다. 구간 경계는 온보딩 선택지와 일치시켰습니다.

```
도보시간      ~10분  0.0      ~20분  0.3      ~30분  0.8      30분+  1.0

도보거리     ~400m  0.0     ~800m  0.3     ~1200m  0.8     1200m+  1.0
```

> [!TIP]
> 가중치를 조정하려면 [`scoring/policy.py`](scoring/policy.py)만 수정하면 됩니다.
> 설계 배경과 판단 근거는 [`docs/design.md`](docs/design.md)에 정리되어 있습니다.

<br>

---

<br>

## 한계

<table>
<tr><td width="240"><b>가중치가 임의값</b></td><td>실제 경로 데이터로 검증 후 조정할 예정입니다.</td></tr>
<tr><td><b>요소 간 상호작용 미반영</b></td><td>선형 가중합이라 계단과 긴 도보가 겹칠 때의 가중된 부담을 산술 합으로만 처리합니다.</td></tr>
<tr><td><b>육교 · 지하보도 접근성</b></td><td>엘리베이터나 경사로 유무를 구분할 수 없어 가중치로 근사합니다.</td></tr>
<tr><td><b>경사 · 오르막 미반영</b></td><td>지도 API가 도보 경로 수준의 경사 데이터를 제공하지 않습니다.</td></tr>
</table>

<br>