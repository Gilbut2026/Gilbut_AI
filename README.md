# Gilbut AI

길벗 서비스의 AI 및 경로 추천 관련 모듈을 관리하는 저장소입니다.

## Modules

### Route Scoring

사용자의 보행 능력, 계단 이용 가능 여부, 보조기구, 환승 및 날씨를 반영해 대중교통 후보 경로를 재정렬합니다.

자세한 사용법은 [`route_scoring/README.md`](route_scoring/README.md)를 참고하세요.

#### FastAPI

```bash
cd route_scoring
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

- `GET /health`: 서버 상태 확인
- `POST /routes/score`: Backend 경로 후보 스코어링

FastAPI는 Backend 요청을 그대로 받고, AI 서버 내부에서 기상청 날씨 정보를 추가한 뒤 기존 `score_routes()`를 호출합니다.
