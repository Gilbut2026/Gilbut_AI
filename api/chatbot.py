"""AI 길벗 채팅 분석기.

백엔드(ChatService)와의 계약:

    요청 : {"sessionId": ..., "state": ..., "message": ..., "context": {...}}
    응답 : {"intent": ..., "action": ..., "value": ..., "referencePlace": ...}


처리 파이프라인 (3단계)
---------------------------------------------------------
1단계  의도 분류    DESTINATION / FACILITY / OUT_OF_SCOPE
2단계  value 추출   목적지명 또는 주변 검색 카테고리
3단계  기준 장소     발화에 기준 장소가 있으면 referencePlace, 없으면 null

1단계 결과에 따라 2, 3단계의 의미가 달라진다.

    DESTINATION  -> value = 목적지명,     referencePlace = 항상 null
    FACILITY     -> value = 검색 카테고리, referencePlace = 기준 장소 또는 null

FACILITY의 검색 카테고리에는 제한이 없다. 병원, 약국, 화장실, 쉼터,
은행, 마트 등 백엔드 장소 검색 API가 받을 수 있는 검색어면 그대로 넘긴다.
"""

import os
import re
import json
from pathlib import Path

from dotenv import load_dotenv


# =========================================================
# 환경변수
# =========================================================

ENV_PATH = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")

_client = None


def _get_client():
    """Gemini 클라이언트를 지연 생성한다 (테스트 시 임포트 부담 제거)."""

    global _client

    if _client is None:
        from google import genai

        api_key = os.getenv("GEMINI_API_KEY")

        if not api_key:
            raise ValueError(
                "GEMINI_API_KEY가 설정되지 않았습니다. api/.env 파일을 확인하세요."
            )

        _client = genai.Client(api_key=api_key)

    return _client


# =========================================================
# 계약 상수
# =========================================================

INTENT_DESTINATION = "DESTINATION"
INTENT_FACILITY = "FACILITY"
INTENT_OUT_OF_SCOPE = "OUT_OF_SCOPE"

ACTION_SEARCH_DESTINATION = "SEARCH_DESTINATION"
ACTION_SEARCH_NEARBY_PLACE = "SEARCH_NEARBY_PLACE"
ACTION_OUT_OF_SCOPE = "OUT_OF_SCOPE"

# intent와 action은 1:1로 묶여 있다.
INTENT_BY_ACTION = {
    ACTION_SEARCH_DESTINATION: INTENT_DESTINATION,
    ACTION_SEARCH_NEARBY_PLACE: INTENT_FACILITY,
    ACTION_OUT_OF_SCOPE: INTENT_OUT_OF_SCOPE,
}


# =========================================================
# 발화 패턴
# =========================================================

# "수원역 근처", "화성행궁 주변" 처럼 기준 장소 뒤에 붙는 표현
_REFERENCE_MARKERS = ("근처", "주변", "인근", "근방")

# 기준 장소 없이 주변 검색임을 알리는 표현
_NEARBY_MARKERS = _REFERENCE_MARKERS + ("가까운", "가까이", "제일 가까운")

_MARKER_PATTERN = re.compile("|".join(_NEARBY_MARKERS))

# 마커 바로 앞의 한 단어를 기준 장소 후보로 잡는다.
_REFERENCE_PATTERN = re.compile(
    r"([^\s]+)\s*(?:" + "|".join(_REFERENCE_MARKERS) + r")"
)

_TRAILING_MARKER_PATTERN = re.compile(
    r"[\s]*(?:" + "|".join(_REFERENCE_MARKERS) + r"|쪽)$"
)

# 기준 장소가 이 표현이면 현재 위치 기준으로 보고 null 처리한다.
CURRENT_LOCATION_WORDS = (
    "내",
    "나",
    "저",
    "제",
    "이",
    "요",
    "여기",
    "여기서",
    "이곳",
    "요기",
    "현재",
    "현위치",
    "현재위치",
    "현재 위치",
    "내 위치",
    "지금",
    "지금 위치",
    "우리 동네",
    "동네",
    "이 근처",
    "가까운",
    "근처",
    "주변",
)

# 기준 장소가 이 표현이면 세션에 저장된 목적지를 기준으로 쓴다.
CONTEXT_DESTINATION_WORDS = (
    "거기",
    "그곳",
    "그 곳",
    "목적지",
    "아까",
    "아까 그곳",
)

_STRIP_CHARS = " \t\n\"'`“”‘’.,!?~"


# =========================================================
# 프롬프트
# =========================================================

PROMPT_TEMPLATE = """당신은 교통약자 길찾기 서비스 'AI 길벗'의 발화 분석기입니다.

사용자 발화를 아래 3단계 순서로 분석합니다.
장소의 실제 존재 여부, 주소, 좌표는 백엔드 장소 검색 API가 확인하므로
당신은 검색어와 기준 장소만 정확히 뽑아냅니다.


[1단계] 의도 분류

먼저 사용자가 무엇을 원하는지 셋 중 하나로 분류합니다.

- DESTINATION
  특정 목적지 한 곳으로 가고 싶다고 말한 경우
  예: "아주대병원 가고 싶어", "수원역으로 가는 길 찾아줘"

- FACILITY
  어떤 위치를 기준으로 그 주변의 장소를 찾아달라고 한 경우
  "근처", "주변", "인근", "가까운" 같은 표현이 단서입니다.
  예: "수원역 근처 병원 찾아줘", "내 근처 약국 알려줘"

- OUT_OF_SCOPE
  목적지 검색도 주변 검색도 아닌 경우
  예: "오늘 날씨 어때?"

지역명만 말하고 "근처" 같은 표현이 없으면 DESTINATION입니다.
예: "수원 버팀병원 가고 싶어" -> DESTINATION


[2단계] value 추출

1단계 결과에 따라 검색어를 뽑습니다.

- DESTINATION 이면 value = 사용자가 말한 목적지명
  예: "나 오늘 아파서 아주대병원에 가고 싶어" -> "아주대병원"

- FACILITY 이면 value = 사용자가 찾는 장소의 카테고리
  예: "수원역 근처 병원 찾아줘" -> "병원"
  예: "화성행궁 주변 화장실" -> "화장실"
  카테고리에는 제한이 없습니다. 병원, 약국, 화장실, 쉼터, 은행,
  마트, 카페 등 사용자가 말한 표현을 그대로 씁니다.

- OUT_OF_SCOPE 이면 value = null

조사, 서술어, 감정, 시간, 이유 표현은 모두 제거합니다.
value에 "근처", "주변" 같은 표현이나 기준 장소명을 넣지 마세요.


[3단계] referencePlace 추출

FACILITY인 경우에만 채웁니다.

- 발화에 기준 장소가 언급되었으면 그 장소명만 넣습니다.
  예: "수원역 근처 병원" -> "수원역"
  예: "화성행궁 주변 화장실" -> "화성행궁"
  "근처", "주변" 같은 표현은 빼고 장소명만 남깁니다.

- 기준이 현재 위치이면 null 입니다.
  예: "내 근처 병원", "이 근처 약국", "가까운 병원" -> null

- DESTINATION 이거나 OUT_OF_SCOPE 이면 항상 null 입니다.


[공통 규칙]

1. 사용자가 말한 장소명을 임의로 교정하지 않습니다.
   ("아주대병원" -> "아주대학교병원" 금지)
2. 사용자가 말하지 않은 지명, 주소, 지점명을 추가하지 않습니다.
3. 확인 질문("이 목적지가 맞나요?")을 하지 않습니다.
   목적지 확정은 백엔드 검색 결과를 받아 프론트에서 처리합니다.
4. 출발지, 출발 시간, 경로 옵션은 추출하지 않습니다.


[intent와 action 조합]

DESTINATION   -> action = SEARCH_DESTINATION
FACILITY      -> action = SEARCH_NEARBY_PLACE
OUT_OF_SCOPE  -> action = OUT_OF_SCOPE


[예시]

발화: "아주대병원 가고 싶어"
{{"intent": "DESTINATION", "action": "SEARCH_DESTINATION", "value": "아주대병원", "referencePlace": null}}

발화: "나 오늘 아파서 아주대병원에 가고 싶어"
{{"intent": "DESTINATION", "action": "SEARCH_DESTINATION", "value": "아주대병원", "referencePlace": null}}

발화: "수원 버팀병원 가야 해"
{{"intent": "DESTINATION", "action": "SEARCH_DESTINATION", "value": "수원 버팀병원", "referencePlace": null}}

발화: "수원역 근처 병원 찾아줘"
{{"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE", "value": "병원", "referencePlace": "수원역"}}

발화: "화성행궁 주변에 정형외과 있어?"
{{"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE", "value": "정형외과", "referencePlace": "화성행궁"}}

발화: "수원역 근처 화장실 어디 있어?"
{{"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE", "value": "화장실", "referencePlace": "수원역"}}

발화: "내 근처 병원 알려줘"
{{"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE", "value": "병원", "referencePlace": null}}

발화: "가까운 약국 가고 싶어"
{{"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE", "value": "약국", "referencePlace": null}}

발화: "오늘 날씨 어때?"
{{"intent": "OUT_OF_SCOPE", "action": "OUT_OF_SCOPE", "value": null, "referencePlace": null}}


[현재 세션 상태]
{state}

[세션에 저장된 목적지]
{destination_name}

[사용자 발화]
{message}

아래 JSON 한 줄만 출력하세요. 코드블록, 설명, 인사말을 붙이지 마세요.
{{"intent": "...", "action": "...", "value": "검색어 또는 null", "referencePlace": "기준 장소명 또는 null"}}
"""


# =========================================================
# Gemini 호출 / 파싱
# =========================================================

def _call_gemini(prompt: str) -> str:
    """SDK 버전에 따라 호출 방식이 달라 둘 다 대응."""

    client = _get_client()

    if hasattr(client, "interactions"):
        interaction = client.interactions.create(
            model=MODEL_NAME,
            input=prompt,
        )
        return (getattr(interaction, "output_text", "") or "").strip()

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
    )
    return (getattr(response, "text", "") or "").strip()


def _extract_json(text: str) -> dict:
    """```json 래핑이나 앞뒤 잡설이 붙어도 JSON 객체만 뽑아낸다."""

    text = re.sub(r"^```[a-zA-Z]*", "", text.strip()).strip()
    text = text.rstrip("`").strip()

    match = re.search(r"\{.*\}", text, re.S)

    if not match:
        raise ValueError(f"JSON을 찾을 수 없습니다: {text}")

    return json.loads(match.group(0))


def _clean(value) -> str | None:
    """
    공백, 따옴표, 문장부호만 정리한다.
    조사를 정규식으로 떼면 '종로', '대학로' 같은 이름이 깨지므로
    조사 제거는 프롬프트에 맡긴다.
    """

    if not isinstance(value, str):
        return None

    cleaned = " ".join(value.split()).strip(_STRIP_CHARS)

    return cleaned or None


def _raw_field(raw: dict, *names) -> str | None:
    """필드명이 흔들려도(value / destination_query / keyword) 흡수한다."""

    slots = raw.get("slots") or {}

    for name in names:
        found = raw.get(name) or slots.get(name)

        if found:
            return found

    return None


# =========================================================
# 1단계 - 의도 분류
# =========================================================

def _has_nearby_marker(message: str) -> bool:
    return bool(_MARKER_PATTERN.search(message or ""))


def _classify(raw: dict, message: str, value: str | None) -> str:
    """
    의도를 분류해 action을 확정한다.

    모델 응답을 우선 신뢰하되,
    - 값이 비었거나
    - 발화에 주변 표현이 있는데 목적지 검색으로 분류된 경우
    를 코드에서 보정한다.
    """

    action = raw.get("action")
    intent = raw.get("intent")

    if action == ACTION_OUT_OF_SCOPE or intent == INTENT_OUT_OF_SCOPE:
        return ACTION_OUT_OF_SCOPE

    if not value:
        return ACTION_OUT_OF_SCOPE

    if action == ACTION_SEARCH_NEARBY_PLACE or intent == INTENT_FACILITY:
        return ACTION_SEARCH_NEARBY_PLACE

    # 모델이 목적지로 봤더라도 value 안에 "근처/주변"이 그대로 남아 있으면
    # 주변 검색을 잘못 분류한 것으로 보고 교정한다.
    if _has_nearby_marker(message) and _MARKER_PATTERN.search(value):
        return ACTION_SEARCH_NEARBY_PLACE

    return ACTION_SEARCH_DESTINATION


# =========================================================
# 2단계 - value 추출
# =========================================================

def _extract_value(raw: dict) -> str | None:
    return _clean(
        _raw_field(
            raw,
            "value",
            "destination_query",
            "keyword",
            "destination",
        )
    )


def _strip_reference_from_value(
    value: str,
    reference_place: str | None,
) -> str:
    """
    value에 기준 장소나 "근처"가 섞여 들어온 경우를 정리한다.

    "수원역 근처 병원" -> "병원"
    """

    if not _MARKER_PATTERN.search(value):
        return value

    cleaned = value

    if reference_place:
        cleaned = cleaned.replace(reference_place, " ")

    cleaned = _MARKER_PATTERN.sub(" ", cleaned)
    cleaned = " ".join(cleaned.split()).strip(_STRIP_CHARS)

    return cleaned or value


# =========================================================
# 3단계 - referencePlace 추출
# =========================================================

def _resolve_reference_place(
    reference_place: str | None,
    context: dict,
) -> str | None:
    """
    기준 장소 표현을 정리한다.

    "수원역 근처" -> "수원역"
    "내", "여기"  -> None (현재 위치 기준)
    "거기"        -> 세션에 저장된 목적지 이름
    """

    reference = _clean(reference_place)

    if not reference:
        return None

    reference = _TRAILING_MARKER_PATTERN.sub("", reference).strip()

    if not reference:
        return None

    if reference in CONTEXT_DESTINATION_WORDS:
        destination = (context or {}).get("destination") or {}
        return _clean(destination.get("name"))

    if reference in CURRENT_LOCATION_WORDS:
        return None

    return reference


def _reference_from_message(message: str) -> str | None:
    """
    모델이 referencePlace를 빠뜨렸을 때 발화에서 직접 찾는다.

    "수원역 근처 병원 찾아줘" -> "수원역"
    "내 근처 병원"            -> None
    """

    match = _REFERENCE_PATTERN.search(message or "")

    if not match:
        return None

    return match.group(1)


def _extract_reference_place(
    raw: dict,
    message: str,
    context: dict,
) -> str | None:
    reference = _resolve_reference_place(
        _raw_field(raw, "referencePlace", "reference_place"),
        context,
    )

    if reference:
        return reference

    return _resolve_reference_place(
        _reference_from_message(message),
        context,
    )


# =========================================================
# 파이프라인
# =========================================================

def _out_of_scope() -> dict:
    return {
        "intent": INTENT_OUT_OF_SCOPE,
        "action": ACTION_OUT_OF_SCOPE,
        "value": None,
        "referencePlace": None,
    }


def _build_result(
    raw: dict,
    message: str = "",
    context: dict | None = None,
) -> dict:
    """모델 응답을 3단계 순서로 정리해 백엔드 계약 형태로 만든다."""

    if not isinstance(raw, dict):
        return _out_of_scope()

    context = context or {}

    # 2단계 결과를 먼저 확보한다. 1단계 보정에 값 유무가 필요하다.
    value = _extract_value(raw)

    # 1단계 - 의도 분류
    action = _classify(raw, message, value)

    if action == ACTION_OUT_OF_SCOPE:
        return _out_of_scope()

    if action == ACTION_SEARCH_DESTINATION:
        return {
            "intent": INTENT_BY_ACTION[action],
            "action": action,
            "value": value,
            "referencePlace": None,
        }

    # 3단계 - 기준 장소
    reference_place = _extract_reference_place(raw, message, context)

    # 2단계 마무리 - value에 섞인 기준 장소/근처 표현 제거
    value = _strip_reference_from_value(value, reference_place)

    return {
        "intent": INTENT_BY_ACTION[action],
        "action": action,
        "value": value,
        "referencePlace": reference_place,
    }


# =========================================================
# 챗봇
# =========================================================

def run_chatbot(
    message: str,
    state: str | dict | None = None,
    context: dict | None = None,
) -> dict:
    """
    사용자 발화를 분석해 백엔드 계약 형태로 반환한다.

    반환 예:
        {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
         "value": "아주대병원", "referencePlace": null}

        {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
         "value": "병원", "referencePlace": "수원역"}
    """

    message = (message or "").strip()

    if not message:
        return _out_of_scope()

    context = context or {}

    destination = context.get("destination") or {}
    destination_name = destination.get("name") or "없음"

    if isinstance(state, dict):
        state_text = json.dumps(state, ensure_ascii=False)
    else:
        state_text = state or "DESTINATION_WAITING"

    prompt = PROMPT_TEMPLATE.format(
        state=state_text,
        destination_name=destination_name,
        message=message,
    )

    text = _call_gemini(prompt)

    if not text:
        raise ValueError("Gemini 응답이 비어 있습니다.")

    try:
        raw = _extract_json(text)

    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(
            f"Gemini 응답을 JSON으로 변환할 수 없습니다: {text}"
        ) from e

    return _build_result(raw, message, context)


# =========================================================
# 로컬 테스트
#
#   python -m api.chatbot            대화형 테스트 (Gemini 호출)
#   python -m api.chatbot --selftest 보정 로직만 오프라인 검증
# =========================================================

def _selftest() -> None:
    """Gemini 없이 파이프라인 보정 로직만 검증한다."""

    cases = [
        (
            "특정 목적지",
            "아주대병원 가고 싶어",
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "아주대병원", "referencePlace": None},
            {},
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "아주대병원", "referencePlace": None},
        ),
        (
            "지역명이 붙은 목적지는 주변 검색이 아니다",
            "수원 버팀병원 가야 해",
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "수원 버팀병원", "referencePlace": None},
            {},
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "수원 버팀병원", "referencePlace": None},
        ),
        (
            "기준 장소 주변 검색",
            "수원역 근처 병원 찾아줘",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "수원역"},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "수원역"},
        ),
        (
            "현재 위치 주변 검색",
            "내 근처 병원 알려줘",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "내"},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": None},
        ),
        (
            "병원 외 카테고리도 주변 검색 가능",
            "수원역 근처 화장실 어디 있어?",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "화장실", "referencePlace": "수원역"},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "화장실", "referencePlace": "수원역"},
        ),
        (
            "기준 장소에 '주변'이 붙어 나온 경우",
            "화성행궁 주변에 정형외과 있어?",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "정형외과", "referencePlace": "화성행궁 주변"},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "정형외과", "referencePlace": "화성행궁"},
        ),
        (
            "모델이 주변 검색을 목적지로 잘못 분류한 경우 교정",
            "수원역 근처 병원 찾아줘",
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "수원역 근처 병원", "referencePlace": None},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "수원역"},
        ),
        (
            "모델이 referencePlace를 빠뜨린 경우 발화에서 보완",
            "화성행궁 근처 약국 알려줘",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "약국", "referencePlace": None},
            {},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "약국", "referencePlace": "화성행궁"},
        ),
        (
            "세션 목적지를 기준으로 삼는 경우",
            "거기 근처 병원 있어?",
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "거기"},
            {"destination": {"name": "수원역"}},
            {"intent": "FACILITY", "action": "SEARCH_NEARBY_PLACE",
             "value": "병원", "referencePlace": "수원역"},
        ),
        (
            "구 필드명(destination_query)으로 와도 흡수",
            "버팀병원 수원 가고 싶어",
            {"intent": "SEARCH_DESTINATION",
             "slots": {"destination_query": "버팀병원 수원"}},
            {},
            {"intent": "DESTINATION", "action": "SEARCH_DESTINATION",
             "value": "버팀병원 수원", "referencePlace": None},
        ),
        (
            "값이 없으면 범위 밖",
            "오늘 날씨 어때?",
            {"intent": "OUT_OF_SCOPE", "action": "OUT_OF_SCOPE",
             "value": None, "referencePlace": None},
            {},
            {"intent": "OUT_OF_SCOPE", "action": "OUT_OF_SCOPE",
             "value": None, "referencePlace": None},
        ),
    ]

    failed = 0

    for name, message, raw, context, expected in cases:
        actual = _build_result(raw, message, context)
        ok = actual == expected

        print(f"[{'PASS' if ok else 'FAIL'}] {name}")

        if not ok:
            failed += 1
            print("  발화:", message)
            print("  기대:", expected)
            print("  실제:", actual)

    print("-" * 50)
    print(f"{len(cases) - failed}/{len(cases)} 통과")


if __name__ == "__main__":

    import sys

    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)

    print("AI 길벗 채팅 분석 테스트")
    print("종료하려면 exit 입력")
    print("=" * 50)

    while True:

        user_input = input("\n사용자: ").strip()

        if user_input.lower() == "exit":
            print("테스트 종료")
            break

        try:
            result = run_chatbot(user_input)

            print("\n[백엔드로 보낼 응답]")
            print(json.dumps(result, ensure_ascii=False, indent=2))

        except Exception as e:
            print("\n오류:", e)