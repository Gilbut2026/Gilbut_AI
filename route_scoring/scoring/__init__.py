"""경로 접근성 스코어링.

거동이 불편한 사용자에게 "가장 빠른 길"이 아니라 "가장 편한 길"을 추천한다.
"""

from .engine import score_routes

__all__ = ["score_routes"]
