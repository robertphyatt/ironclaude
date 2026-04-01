#!/usr/bin/env python3
"""Analyze token usage and cost from Claude Code session transcripts.

Usage:
    python3 scripts/analyze-token-usage.py <session.jsonl>
    python3 scripts/analyze-token-usage.py ~/.claude/projects/.../transcript.jsonl

Parses session JSONL files and produces per-agent cost breakdown.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

# Pricing per million tokens (Claude Opus 4.6 as of 2026-02)
PRICE_INPUT = 15.00
PRICE_OUTPUT = 75.00
PRICE_CACHE_READ = 1.875


def analyze_transcript(path: str) -> None:
    agents = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "description": ""})

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Extract usage data
            usage = entry.get("usage", {})
            if not usage:
                continue

            agent_id = entry.get("agentId", entry.get("sessionId", "main"))
            if agent_id is None:
                agent_id = "main"

            # Shorten agent IDs for display
            display_id = str(agent_id)[:8] if len(str(agent_id)) > 8 else str(agent_id)

            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            cache_read = usage.get("cache_read_input_tokens", usage.get("cache_read", 0))

            agents[display_id]["input"] += input_tokens
            agents[display_id]["output"] += output_tokens
            agents[display_id]["cache_read"] += cache_read

            # Try to capture description from tool use context
            msg = entry.get("message", {})
            if isinstance(msg, dict):
                content = msg.get("content", [])
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            name = block.get("name", "")
                            if name and not agents[display_id]["description"]:
                                agents[display_id]["description"] = name

    if not agents:
        print(f"No usage data found in {path}")
        return

    # Calculate costs
    print(f"\nToken Usage Analysis: {Path(path).name}")
    print("=" * 90)
    print(f"{'Agent':<12} {'Description':<35} {'Input':>8} {'Output':>8} {'Cache':>10} {'Cost':>8}")
    print("-" * 90)

    total_cost = 0.0
    for agent_id, data in sorted(agents.items(), key=lambda x: x[1]["input"] + x[1]["output"], reverse=True):
        cost = (
            data["input"] * PRICE_INPUT / 1_000_000
            + data["output"] * PRICE_OUTPUT / 1_000_000
            + data["cache_read"] * PRICE_CACHE_READ / 1_000_000
        )
        total_cost += cost
        desc = data["description"][:33] if data["description"] else ""
        print(f"{agent_id:<12} {desc:<35} {data['input']:>8,} {data['output']:>8,} {data['cache_read']:>10,} ${cost:>6.2f}")

    print("-" * 90)
    total_input = sum(d["input"] for d in agents.values())
    total_output = sum(d["output"] for d in agents.values())
    total_cache = sum(d["cache_read"] for d in agents.values())
    print(f"{'TOTAL':<12} {'':<35} {total_input:>8,} {total_output:>8,} {total_cache:>10,} ${total_cost:>6.2f}")
    print()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    analyze_transcript(sys.argv[1])
