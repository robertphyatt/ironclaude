from __future__ import annotations

from pathlib import Path


class CommunicationProfileError(RuntimeError):
    pass


CONSTRUCTION_PROFILES = {
    "direct_professional_session": "human",
    "commander_brain": "mixed",
    "managed_worker": "ai",
    "advisor": "ai",
    "claude_grader": "ai",
    "codex_grader": "ai",
    "local_grader": "ai",
    "shadow_grader": "ai",
    "session_summarizer": "ai",
    "hook_validator": "machine",
}

_PROFILE_SKILLS = {
    "human": ("elements-of-style",),
    "ai": ("write-lossless-ai-messages",),
    "machine": (),
    "mixed": ("elements-of-style", "write-lossless-ai-messages"),
}
_DEFAULT_SKILLS_ROOT = Path(__file__).resolve().parents[3] / "worker" / "skills"
PROFILE_READY_MARKER = "IC_LOSSLESS_AI_MESSAGES_ACTIVE"


def load_profile(profile: str, *, skills_root: Path | None = None) -> str:
    names = _PROFILE_SKILLS.get(profile)
    if names is None:
        raise CommunicationProfileError(f"unknown communication destination: {profile}")
    root = skills_root or _DEFAULT_SKILLS_ROOT
    blocks = []
    for name in names:
        path = root / name / "SKILL.md"
        try:
            blocks.append(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise CommunicationProfileError(
                f"communication skill missing or unreadable: {path}: {exc}"
            ) from exc
    return "\n\n".join(blocks)


def apply_communication_profile(construction_path: str, system_prompt: str) -> str:
    try:
        profile = CONSTRUCTION_PROFILES[construction_path]
    except KeyError as exc:
        raise CommunicationProfileError(
            f"unclassified LLM construction path: {construction_path}"
        ) from exc
    policy = load_profile(profile)
    if not policy:
        return system_prompt
    routing = (
        "Apply communication policy by destination: human -> elements-of-style; "
        "AI -> write-lossless-ai-messages; machine -> declared schema unchanged. "
        "This policy was loaded programmatically: Do not emit "
        "IC_LOSSLESS_AI_MESSAGES_ACTIVE. That marker is only for explicit "
        "interactive managed-worker skill activation."
    )
    return f"{policy}\n\n{routing}\n\n{system_prompt}"


def skill_invocation(construction_path: str, client: str) -> str:
    profile = CONSTRUCTION_PROFILES.get(construction_path)
    if profile != "ai":
        raise CommunicationProfileError(
            f"construction path does not select AI profile: {construction_path}"
        )
    load_profile(profile)
    if client == "claude":
        return "/write-lossless-ai-messages"
    if client == "codex":
        return "$ironclaude:write-lossless-ai-messages"
    raise CommunicationProfileError(f"unsupported communication client: {client}")
