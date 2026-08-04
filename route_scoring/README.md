<div align="center">

# 경로 접근성 스코어링

**"가장 빠른 길"이 아니라 "가장 편한 길"을 추천합니다**

거동이 불편한 사용자를 위한 경로 재평가 엔진

![tests](https://img.shields.io/badge/tests-34%20passed-brightgreen)
![python](https://img.shields.io/badge/python-3.9+-blue)
![dependencies](https://img.shields.io/badge/dependencies-none-lightgrey)

</div>

---

지도 API는 기본적으로 최단 시간 경로를 우선합니다. 계단이 세 개 있어도 5분 빠르면 그 길을 추천하죠. 이 모듈은 사용자의 보행 능력을 기준으로 후보 경로를 다시 평가합니다.

```
                     ┌──────────────┐
   경로 후보 ───────▶ │ Hard Filter  │  통행 불가 경로 제외
                     └──────┬───────┘
                            ▼
                     ┌──────────────┐
                     │  점수 계산    │  사용자 능력 기반 가중합
                     └──────┬───────┘
                            ▼
                     ┌──────────────┐
                     │    정렬       │  동점 시 장애물 우선
                     └──────┬───────┘
                            ▼
                     ┌──────────────┐
                     │  DRT 판단     │  똑버스 / 콜택시 안내
                     └──────┬───────┘
                            ▼
                          결과
```

<br>

## 시작하기

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

<details>
<summary><b>응답 예시 보기</b></summary>

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
    },
    {
      "candidateId": "route-002",
      "status": "FILTERED",
      "score": null,
      "rank": null,
      "filterCodes": ["STAIR_UNAVAILABLE_WITH_EXTERNAL_STAIR"]
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

</details>

전체 입출력 예시는 [`examples/`](examples/)를 참고하세요.

<br>

## 구조

```
scoring/
├── engine.py       진입점 · 검증 → 필터 → 점수 → 정렬 → DRT 조합
├── policy.py       가중치와 상수 · 튜닝은 이 파일만 수정
├── validation.py   입력 검증 · 필수 필드와 candidateId 확인
├── filters.py      Hard Filter · 통행 불가 경로 제외
├── score.py        점수 계산 · 정규화와 가중합
├── obstacles.py    도보 구간의 계단·육교·지하보도 집계
└── drt.py          똑버스 / 교통약자 콜택시 안내 판단
```

<br>

## 점수 계산

```
score = -(도보시간 + 도보거리 + 장애물 + 환승 + 날씨)
```

점수가 높을수록(0에 가까울수록) 편한 경로입니다. 각 항목의 가중치는 사용자 응답에 따라 달라집니다.

<table>
<tr><th align="left">항목</th><th align="left">가중치 결정 기준</th></tr>
<tr><td>도보시간·거리</td><td>한 번에 걸을 수 있는 시간</td></tr>
<tr><td>장애물</td><td>계단 이용 가능 수준 × 보조기구 사용 여부</td></tr>
<tr><td>환승</td><td>환승 가능 수준</td></tr>
<tr><td>날씨</td><td>날씨 등급 × 도보거리</td></tr>
</table>

> 날씨는 도보거리와 함께 작용합니다. 같은 비 오는 날이라도 밖에 오래 있어야 하는 경로가 더 불리합니다.

### 구간별 계단식 정규화

선형 정규화는 체감의 비선형성을 반영하지 못합니다. 도보 5분과 10분의 차이는 크지만 40분과 45분의 차이는 작은데, 선형은 이를 같은 크기로 취급하니까요.

```
도보시간    ~10분 0.0   │   ~20분 0.3   │   ~30분 0.8   │   30분+ 1.0
도보거리   ~400m 0.0   │  ~800m 0.3   │  ~1200m 0.8   │  1200m+ 1.0
```

구간 경계는 온보딩 선택지(`10분 이내 / 20분 정도 / 30분 이상`)와 일치시켜 임의로 정하지 않았습니다.

<br>

## 설계 판단

### 통행 불가는 감점이 아니라 제외로 처리합니다

계단을 이용할 수 없는 사용자에게 계단이 있는 경로는 "불편"이 아니라 "통행 불가"입니다. 감점만 하면 다른 조건이 좋을 때 통행 불가 경로가 1순위가 될 수 있습니다.

다만 **환승 불가는 필터가 아닌 강한 감점**(가중치 5.0)으로 처리합니다. 필터로 걸면 후보가 전부 사라질 수 있기 때문입니다.

### 조회 실패(UNKNOWN)는 사용자에 따라 다르게 처리합니다

<table>
<tr><th align="left">사용자</th><th align="left">Hard Filter</th><th align="left">점수</th></tr>
<tr><td>휠체어</td><td>제외</td><td>—</td></tr>
<tr><td>계단 이용 불가</td><td>통과</td><td>있다고 가정하여 감점</td></tr>
</table>

휠체어 사용자에게 계단은 물리적으로 통행이 불가능하고 되돌릴 수 없으므로, 확실하지 않을 때는 안전하게 판단합니다.

반면 조회 실패를 모두 제외하면, 실제로는 계단이 없던 경로가 API 오류만으로 후보에서 사라집니다. 점수에서 불리하게 두되 후보로는 남기는 편이 손해가 적습니다.

### 육교·지하보도는 제외 대상이 아닙니다

Hard Filter는 **계단만** 봅니다. 육교나 지하보도에는 엘리베이터나 경사로가 있을 수 있는데, 현재 데이터로는 구분할 수 없습니다. 일괄 제외하면 실제로는 갈 수 있는 경로까지 잃게 됩니다.

대신 점수에서 계단보다 낮은 가중치로 감점합니다.

```
stair 1.0   │   overpass 0.7   │   underpass 0.7
```

접근 가능 여부를 알 수 없어서 쓰는 근사치입니다. 백엔드에서 `accessible` 같은 신호를 전달할 수 있게 되면, 이 가중치 대신 실제 값으로 판단해야 합니다.

### 조회 실패는 구간 단위로 한 번만 감점합니다

한 구간에서 계단·육교·지하보도가 모두 `UNKNOWN`인 것은 장애물이 세 개 있다는 뜻이 아니라, 그 구간 조회가 통째로 실패했다는 뜻입니다. 종류별로 각각 감점하면 과도하므로 구간당 한 번만 적용합니다.

### 역 내부 계단은 장애물로 보지 않습니다

수원 관내 지하철역 14곳을 전수 조사한 결과 모든 역에 엘리베이터가 있었습니다. 따라서 `segmentScope: "STATION_INTERNAL"` 구간은 엘리베이터로 이동 가능하다고 가정하고 계산에서 제외합니다.

### 휠체어 사용자에게는 똑버스를 안내하지 않습니다

수원 관내 똑버스에는 휠체어 탑승 가능 차량이 없습니다(경기교통공사 확인). 탈 수 없는 수단을 추천하지 않기 위해, 대신 교통약자 콜택시를 안내합니다.

### 값이 없는 후보는 계산하지 않습니다

누락된 값을 0으로 처리하면, API 오류로 정보가 비어 있는 경로가 오히려 "도보 0분, 환승 0회"로 계산되어 1위가 됩니다. 필수 필드가 없으면 `VALIDATION_ERROR`를 반환합니다.

### 동점 처리

점수가 같으면 **장애물 → 도보시간 → 환승 → providerRank** 순으로 정합니다. 장애물을 첫 기준으로 둔 것은 이 서비스의 기준이 "빠른 길"이 아니라 "편한 길"이기 때문입니다.

<br>

## 테스트

```bash
python tests/test_scoring.py
```

```
34/34 passed
```

<br>

## 알려진 한계

| 한계 | 내용 |
|---|---|
| **요소 간 상호작용 미반영** | 선형 가중합은 "계단 3개 + 긴 도보"가 동시에 있을 때의 가중된 부담을 산술 합으로만 처리합니다. 관련 연구에서도 공통적으로 지적되는 한계입니다. |
| **가중치가 임의값** | 실제 경로 데이터로 검증 후 조정할 예정입니다. |
| **경사·오르막 미반영** | 지도 API가 도보 경로 수준의 경사 데이터를 제공하지 않습니다. |
| **육교·지하보도 접근성 불명** | 엘리베이터나 경사로 유무를 구분할 수 없어 가중치로 근사합니다. 휠체어 사용자에게는 이 구분이 통행 가능 여부를 가르므로, 신호가 확보되면 우선 반영해야 합니다. |
| **점수가 음수** | `-1.4`가 `-3.2`보다 좋은 경로라 직관적이지 않습니다. 백엔드 연동 스펙에 `score`로 정의되어 있어 현재는 유지하며, 가중치 검증 후 표현 방식을 함께 논의할 예정입니다. |

<br>

## 참고 문헌

- Gharebaghi et al. (2021), *User-Specific Route Planning for People with Motor Disabilities: A Fuzzy Approach*, ISPRS IJGI 10(2)
- Kasemsuppakorn & Karimi (2009) — AHP 기반 보도 임피던스 산정
- Neis (2015) — VGI 기반 경로 계획의 신뢰도 측정
