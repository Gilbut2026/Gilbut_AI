import os
import json
from pathlib import Path

from google import genai
from dotenv import load_dotenv


# =========================================================
# 환경변수 로드
# =========================================================

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY가 설정되지 않았습니다. api/.env 파일을 확인하세요."
    )


# Gemini Client
client = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# 챗봇
# =========================================================

def run_chatbot(
    stt_text: str,
    state: dict | None = None
) -> dict:

    state = state or {}

    prompt = f"""
당신은 교통약자 이동상담 앱 'AI 길벗'의 챗봇입니다.

사용자의 발화와 현재 대화 상태를 분석하여
반드시 JSON으로만 반환하세요.

절대 추측하거나 없는 정보를 만들지 마세요.

사용 가능한 intent:
- SET_DESTINATION
- SEARCH_ROUTE
- SEARCH_RESTROOM
- SEARCH_SHELTER
- GET_WEATHER
- REQUEST_DRT
- CHECK_ELEVATOR
- UPDATE_CONDITION
- UNKNOWN

각 intent의 의미:
- SET_DESTINATION: 사용자가 목적지를 말하거나 변경함
- SEARCH_ROUTE: 목적지까지 이동 경로를 요청함
- SEARCH_RESTROOM: 화장실 위치 또는 정보를 요청함
- SEARCH_SHELTER: 쉼터 위치 또는 정보를 요청함
- GET_WEATHER: 날씨 정보를 요청함
- REQUEST_DRT: DRT, 똑버스, 콜택시 등 이동지원 차량을 요청함
- CHECK_ELEVATOR: 지하철역 엘리베이터 정보를 요청함
- UPDATE_CONDITION: 현재 몸 상태나 이동 상황을 말함
- UNKNOWN: 위 intent에 해당하지 않음

추출 항목:
- destination: 목적지 장소명
- condition: 오늘 컨디션
  ("무릎 통증" / "숨참" / "짐 있음" / "더위 약함" / "길찾기 불안" / null)
- departure_time: 출발 시간
  ("오늘" / "지금" / 구체적 시간 / null)
- is_alone: 혼자 이동 여부
  (true / false / null)

현재 대화 상태:
{json.dumps(state, ensure_ascii=False)}

사용자 발화:
{stt_text}

반드시 아래 JSON 형식으로만 반환하세요.

{{
  "intent": "의도",
  "slots": {{
    "destination": null,
    "condition": null,
    "departure_time": null,
    "is_alone": null
  }},
  "message": "사용자에게 보여줄 짧은 응답"
}}

추가 규칙:
- 실제 경로 시간, 거리, 날씨, 시설 위치 등 외부 데이터 조회가 필요한 정보는 절대 만들어내지 마세요.
- 데이터 조회가 필요한 경우 "찾아볼게요", "확인해볼게요" 정도로만 응답하세요.
- 사용자가 명확하게 말하지 않은 목적지는 추측하지 마세요.
- "병원", "역", "화장실"처럼 특정 장소명이 아닌 표현만 나온 경우 목적지를 임의로 정하지 마세요.
- 현재 대화 상태에 이미 목적지가 있다면 필요할 때 참고할 수 있습니다.
"""

    # =====================================================
    # Gemini 호출
    # =====================================================

    interaction = client.interactions.create(
        model="gemini-3.6-flash",
        input=prompt
    )

    text = (interaction.output_text or "").strip()

    if not text:
        raise ValueError("Gemini 응답이 비어 있습니다.")

    # ```json ... ``` 제거
    text = text.replace("```json", "")
    text = text.replace("```", "")
    text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Gemini 응답을 JSON으로 변환할 수 없습니다: {text}"
        ) from e

    return result