<div align="center">

# 🤖 Gilbut AI

길벗 서비스의 AI 및 경로 추천 관련 모듈을 관리하는 저장소입니다.

</div>

---

## 🧩 Modules

### 🧭 Route Scoring

사용자의 보행 능력, 계단 이용 가능 여부, 보조기구, 환승 및 날씨를 반영해 대중교통 후보 경로를 재정렬합니다.

자세한 사용법은 [`route_scoring/README.md`](route_scoring/README.md)를 참고하세요.

### ⚡ FastAPI

Backend 연동용 FastAPI 서버는 Route Scoring과 분리하여 [`api/`](api/)에 정리되어 있습니다.

```bash
pip install -r api/requirements.txt
cp api/.env.example api/.env
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

- `GET /health`: 서버 상태 확인
- `POST /routes/score`: Backend 경로 후보 스코어링

Backend에서는 `AI_SCORING_URL`을 실행 중인 AI 서버의 `/routes/score` 전체 URL로 설정하면 됩니다.

Backend는 사용자 온보딩 정보와 경로 후보(`candidates`, `walkSegments`)를 전달합니다. 날씨 `environment`는 Backend 입력이 아니라 FastAPI가 기상청 API를 조회해 AI 내부에서 생성한 뒤 `route_scoring.scoring.score_routes()`에 추가합니다.

FastAPI의 요청 흐름, `walkSegments` 처리, 날씨 조회, 응답 계약은 [`api/README.md`](api/README.md)를 참고하세요.
