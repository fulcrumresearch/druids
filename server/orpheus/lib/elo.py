"""ELO rating computation and comparison recording."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from orpheus.db.models.execution import ExecutionRecord
from orpheus.db.models.spec import compute_spec_hash, get_spec_by_hash, upsert_spec


logger = logging.getLogger(__name__)


def update_elo(winner_rating: float, loser_rating: float, k: float = 32.0) -> tuple[float, float]:
    """Compute new ELO ratings after a single pairwise matchup.

    Returns (new_winner_rating, new_loser_rating).
    """
    expected_w = 1.0 / (1.0 + 10 ** ((loser_rating - winner_rating) / 400.0))
    expected_l = 1.0 - expected_w
    new_w = winner_rating + k * (1.0 - expected_w)
    new_l = loser_rating + k * (0.0 - expected_l)
    return new_w, new_l


async def record_comparison(
    db: AsyncSession,
    winner: ExecutionRecord,
    losers: list[ExecutionRecord],
) -> None:
    """Update ELO ratings on Spec rows for all pairwise matchups between winner and losers.

    Only compares specs when both winner and loser have a program_spec set.
    """
    if not winner.program_spec:
        logger.info("Winner %s has no program_spec, skipping ELO update", winner.slug)
        return

    w_hash = compute_spec_hash(winner.program_spec)
    w_spec = await get_spec_by_hash(db, w_hash)
    if not w_spec:
        w_spec = await upsert_spec(db, w_hash, winner.program_name, winner.program_spec)

    for loser in losers:
        if not loser.program_spec:
            continue

        l_hash = compute_spec_hash(loser.program_spec)
        l_spec = await get_spec_by_hash(db, l_hash)
        if not l_spec:
            l_spec = await upsert_spec(db, l_hash, loser.program_name, loser.program_spec)

        new_w, new_l = update_elo(w_spec.rating, l_spec.rating)
        w_spec.rating = new_w
        w_spec.num_comparisons += 1
        w_spec.updated_at = datetime.now(timezone.utc)
        l_spec.rating = new_l
        l_spec.num_comparisons += 1
        l_spec.updated_at = datetime.now(timezone.utc)

    await db.flush()
    logger.info(
        "Recorded ELO comparison: winner=%s losers=%s",
        winner.slug,
        [l.slug for l in losers],
    )
