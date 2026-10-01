"""Buffer sizing (the cut-and-past / 50% rule) and its provenance.

Kept separate from the scheduler so the sizing policy can be reviewed and
changed independently of resource leveling / critical-chain detection.
"""
from __future__ import annotations


def buffer_basis(chain_ids: list[str], tasks: dict) -> list[dict]:
    """Per-task terms feeding the buffer formula of one chain."""
    return [{"task_id": tid,
             "aggressive_days": tasks[tid]["agg"],
             "conservative_days": tasks[tid]["cons"],
             "delta_days": tasks[tid]["cons"] - tasks[tid]["agg"]}
            for tid in chain_ids]


def buffer_size(basis: list[dict]) -> float:
    """PB/FB = 0.5 * sum(conservative - aggressive).

    See docs/DECISIONS.md for why the 0.5 cut-and-past factor is used.
    """
    return 0.5 * sum(b["delta_days"] for b in basis)
