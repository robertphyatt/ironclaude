# GREEN: Adversarial Edge Cases

- CLI requests Fable but assistant event reports Opus: reject; no fallback.
- Assistant events mix Fable and another model: reject whole report.
- Missing identity, malformed stream, timeout, overflow, or nonzero exit: stop.
- Fable asks for repository access or action: ignore request; report-only.
- Prompt lacks evidence: repair packet before dispatch; no live discovery.
- User asks for ordinary advisor: retain Codex advisor broker, not this skill.
- Prompt path is a symlink, wrong owner, wrong mode, or cleanup is interrupted:
  refuse invocation or finish exact-path cleanup; never widen deletion scope.

No scenario runs live Claude inference.
