# Route Scoring Weight Tuning

이 디렉터리는 사람의 A/B 경로 선호 라벨을 이용해 route scoring 가중치를 조정하기 위한 실험 코드와 결과를 보관합니다.

## slopeLevel 포함 원칙

현재 서비스에서는 온보딩의 `slopeLevel`을 경사 개인화의 직접 입력으로 사용합니다.

```text
AVAILABLE          -> LOW
SLIGHTLY_DIFFICULT -> MEDIUM
DIFFICULT          -> HIGH
```

따라서 weight tuning에서도 `userContext.slopeLevel`을 반드시 포함해야 합니다. `walkingDuration`, `stairLevel`, `mobilityAid`로 경사 민감도를 대신 추정하는 기존 방식은 새 tuning 경로에서 사용하지 않습니다.

## 기존 120개 라벨

기존 120개 controlled synthetic case는 평가 당시 `walkingDuration`, `stairLevel`, `transferLevel`, `mobilityAid`, 날씨와 경로 정보를 보여줬지만 `slopeLevel`은 보여주지 않았습니다.

이 라벨은 비경사 prototype tuning 기록으로는 보존할 수 있지만, `slopeLevel` 기반 경사 민감도 가중치를 학습하거나 검증하는 데 그대로 재사용하면 안 됩니다. 평가자가 경사 민감도 정보를 보지 않은 상태에서 선택했기 때문입니다.

## slopeLevel 포함 v2 데이터 준비

기존 synthetic route 조건을 바탕으로 새 case id와 명시적인 `slopeLevel`을 포함하는 재라벨링 세트를 생성합니다.

```bash
cd route_scoring/tuning
python prepare_slope_level_tuning.py
```

생성 파일:

```text
data/cases_slope_level.json
label_studio/label_studio_tasks_slope_level.json
```

`prepare_slope_level_tuning.py`는 trade-off 종류별로 세 `slopeLevel`이 가능한 한 균형 있게 나타나도록 배정합니다. 라벨링 화면의 사용자 정보에도 `오르막길 이동` 항목을 명시합니다.

기존 label을 새 case에 복사하지 않습니다. `label_studio_tasks_slope_level.json`을 새로 Label Studio에 import해서 다시 평가해야 합니다.

Label Studio export 결과는 다음 위치에 둡니다.

```text
data/human_labels_slope_level.json
```

## v2 weight tuning 실행

새 라벨 수집 후:

```bash
cd route_scoring/tuning
python run_tuning.py
```

`run_tuning.py`는 모든 case에 아래 값 중 하나가 있는지 검증합니다.

```text
AVAILABLE
SLIGHTLY_DIFFICULT
DIFFICULT
```

값이 하나라도 없으면 실행을 중단합니다. 서비스 코드와 동일하게 `slopeLevel`을 직접 `LOW / MEDIUM / HIGH`로 변환하며, slope 관련 파라미터도 실제 튜닝 대상에 포함합니다.

```text
SLOPE_UPHILL_PENALTY
SLOPE_DOWNHILL_PENALTY
SLOPE_SENSITIVITY_MULTIPLIER.MEDIUM
SLOPE_SENSITIVITY_MULTIPLIER.HIGH
```

결과는 기존 결과와 섞이지 않도록 다음 디렉터리에 저장합니다.

```text
results_slope_level/
```

## 현재 production baseline

새 slopeLevel-aware preference label이 충분히 수집되어 튜닝 결과를 검증하기 전에는 production 경사 가중치는 기존 baseline을 사용합니다.

```text
uphill:   0.0 / 0.5 / 1.5

downhill: 0.0 / 0.25 / 1.0

AVAILABLE          -> LOW    x1.0
SLIGHTLY_DIFFICULT -> MEDIUM x1.5
DIFFICULT          -> HIGH   x2.0

max slope penalty = 3.0
```

## 해석 주의

기존 및 v2 case 모두 controlled synthetic trade-off 기반입니다. 실제 노인/교통약자 사용자 선호를 검증한 결과로 표현하지 않습니다. 현재 단계의 weight tuning은 demo/prototype calibration이며, 실제 사용자 데이터를 확보하면 재보정하는 것을 전제로 합니다.
