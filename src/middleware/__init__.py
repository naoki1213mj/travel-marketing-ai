"""軽量な入力 / ツール応答ガード。"""

import re
from dataclasses import dataclass

_PROMPT_INJECTION_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"ignore (all )?(previous|prior) instructions", re.IGNORECASE), "ignore_previous_instructions"),
    (re.compile(r"(system|developer) prompt", re.IGNORECASE), "prompt_exfiltration_attempt"),
    (re.compile(r"reveal .*instructions", re.IGNORECASE), "instruction_reveal_attempt"),
    (re.compile(r"do not follow .*instructions", re.IGNORECASE), "instruction_override_attempt"),
    (re.compile(r"you are now|act as", re.IGNORECASE), "persona_override_attempt"),
    (re.compile(r"jailbreak|prompt injection", re.IGNORECASE), "jailbreak_attempt"),
    (re.compile(r"call the tool|use the tool|run the tool", re.IGNORECASE), "tool_override_attempt"),
]


@dataclass
class ShieldResult:
    """入力 / ツール応答ガードの判定結果。"""

    is_safe: bool
    details: dict | None = None


def _detect_prompt_injection(text: str) -> dict | None:
    """明らかなプロンプト注入パターンをローカルで検出する。"""
    matches = [reason for pattern, reason in _PROMPT_INJECTION_PATTERNS if pattern.search(text)]
    if not matches:
        return None
    return {"reason": "prompt_injection_detected", "signals": matches}


async def check_prompt_shield(user_input: str) -> ShieldResult:
    """ユーザー入力に対して軽量な注入ガードを適用する。"""
    injection_details = _detect_prompt_injection(user_input)
    if injection_details is not None:
        return ShieldResult(is_safe=False, details=injection_details)
    return ShieldResult(is_safe=True)


async def check_tool_response(tool_output: str) -> ShieldResult:
    """外部ツール応答に対して軽量な注入ガードを適用する。"""
    injection_details = _detect_prompt_injection(tool_output)
    if injection_details is not None:
        return ShieldResult(is_safe=False, details=injection_details)
    return ShieldResult(is_safe=True)
