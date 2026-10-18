"""Design 2026-10-17 v120-completeness R2: IronClaude pins no Codex model as a default or a
fallback. Family words resolve where Codex runs; tests may still use concrete slugs."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
RUNTIME_SOURCES = (
    "commander/src/ironclaude/config.py",
    "commander/src/ironclaude/provider_config.py",
    "commander/src/ironclaude/provider_router.py",
    "commander/src/ironclaude/codex_brain_client.py",
    "commander/src/ironclaude/fast_model.py",
    "commander/src/ironclaude/orchestrator_mcp.py",
    "worker/hooks/plan-validator.sh",
    "worker/mcp-servers/state-manager/src/tools/advisor-review.ts",
    "worker/mcp-servers/state-manager/dist/index.js",
    "config/ironclaude.json.example",
)
SLUG = re.compile(r"gpt-\d+(?:\.\d+)*-(?:luna|terra|sol|astra)")


def test_no_pinned_codex_model_slugs_in_runtime_sources():
    found = {rel: SLUG.findall((REPO / rel).read_text()) for rel in RUNTIME_SOURCES}
    assert {rel: hits for rel, hits in found.items() if hits} == {}
