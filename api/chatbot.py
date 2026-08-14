import os
import json

from google import genai
from dotenv import load_dotenv


# =========================================================
# 환경변수 로드
# =========================================================

load_dotenv("api/.env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not GEMINI_API_KEY:
    raise ValueError(
        "GEMINI_API_KEY가 설정되지 않았습니다. api/.env 파일을 확인하세요."
    )


# GEMINI_API_KEY 환경변수를 자동으로 사용
client = genai.Client()


# =========================================================
# 챗봇
# =========================================================

def run_chatbot(stt_text: str, state: dict | None = None) -> dict:
    state = state or {}

    prompt = f"""
당신은 교통약자 이동상담 앱 'AI 길벗'의 챗봇입니다.

사용자의 발화를 분석하여 반드시 JSON으로만 반환하세요.
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

    # 혹시 Gemini가 ```json ... ``` 형태로 반환할 경우 제거
    text = text.replace("```json", "")
    text = text.replace("```", "")
    text = text.strip()

    # JSON 문자열 → Python dict
    return json.loads(text)


# =========================================================
# 단독 테스트
# =========================================================

if __name__ == "__main__":
    result = run_chatbot(
        "오늘 무릎이 아픈데 아주대병원 가고 싶어"
    )

    print(
        json.dumps(
            result,
            ensure_ascii=False,
            indent=2
        )
    )