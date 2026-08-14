# Route Scoring Weight Tuning

이 디렉터리는 사람의 A/B 경로 선호 라벨을 이용해 route scoring 가중치를 조정하기 위한 실험 코드와 결과를 보관합니다.

## 현재 데이터의 범위

현재 저장된 120개 라벨 case는 `walkingDuration`, `stairLevel`, `transferLevel`, `mobilityAid`, 날씨, 경로별 도보/장애물/경사 정보를 제시해 평가했습니다. 당시 Backend의 `slopeLevel`이 아직 AI 경사 스코어링에 직접 연결되기 전이어서, 라벨 화면에는 `slopeLevel`이 포함되지 않았습니다.

따라서 현재 라벨로는 `AVAILABLE / SLIGHTLY_DIFFICULT / DIFFICULT`라는 새 `slopeLevel` 의미에 대한 경사 민감도 가중치를 직접 검증했다고 볼 수 없습니다.

## 실행 원칙

현재 데이터로 다시 튜닝할 때는 다음 명령을 사용합니다.

```bash
cd route_scoring/tuning
python run_tuning.py
```

`run_tuning.py`는 서비스 코드와 동일하게 `slopeLevel`을 우선 해석합니다. 그러나 현재 case에 명시적 `slopeLevel`이 없으므로 아래 경사 관련 파라미터는 튜닝 대상에서 자동 제외하고 baseline으로 고정합니다.

- `SLOPE_UPHILL_PENALTY`
- `SLOPE_DOWNHILL_PENALTY`
- `SLOPE_SENSITIVITY_MULTIPLIER`

현재 경사 baseline은 다음과 같습니다.

```text
uphill:   0.0 / 0.5 / 1.5

downhill: 0.0 / 0.25 / 1.0

AVAILABLE          -> LOW    x1.0
SLIGHTLY_DIFFICULT -> MEDIUM x1.5
DIFFICULT          -> HIGH   x2.0

max slope penalty = 3.0
```

## slope 가중치를 다시 튜닝하려면

새 preference case를 만들 때 `userContext.slopeLevel`을 반드시 포함하고, 라벨링 화면의 사용자 정보에도 해당 값을 명시해야 합니다. 그 상태에서 새 라벨을 수집한 뒤 `run_tuning.py`를 실행하면 slope 관련 파라미터도 다시 튜닝 대상에 포함됩니다.

기존 120개 라벨에 사후적으로 `slopeLevel`을 임의 추가해 slope 가중치를 재학습하는 것은 권장하지 않습니다. 평가자가 해당 정보를 보지 않고 선택한 라벨이기 때문에 새 온보딩 의미를 검증한 데이터로 해석할 수 없기 때문입니다.

## 현재 tuned weight 해석

현재 저장된 tuned 결과는 단일 evaluator의 120개 controlled synthetic trade-off case를 기반으로 한 prototype calibration입니다. 실제 노인/교통약자 사용자 선호를 검증한 결과로 표현하지 않습니다. 서비스 기능 완료 및 데모에는 사용할 수 있지만, 실제 사용자 데이터가 확보되면 재보정하는 것을 전제로 합니다.
