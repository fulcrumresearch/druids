"""Tests for ELO rating computation."""

from orpheus.lib.elo import update_elo
from orpheus.db.models.spec import compute_spec_hash


class TestUpdateElo:
    def test_equal_ratings(self):
        """Equal-rated players: winner gains 16, loser loses 16."""
        new_w, new_l = update_elo(1500.0, 1500.0)
        assert new_w == 1516.0
        assert new_l == 1484.0

    def test_favorite_wins(self):
        """When the higher-rated player wins, the adjustment is small."""
        new_w, new_l = update_elo(1800.0, 1200.0)
        # Favorite expected to win, so small gain
        assert new_w > 1800.0
        assert new_w < 1801.0
        assert new_l < 1200.0
        assert new_l > 1199.0

    def test_underdog_wins(self):
        """When the lower-rated player wins, the adjustment is large."""
        new_w, new_l = update_elo(1200.0, 1800.0)
        # Underdog wins -- large gain
        assert new_w > 1230.0
        assert new_l < 1770.0

    def test_sum_preserved(self):
        """Total rating points are preserved (zero-sum)."""
        for w, l in [(1500, 1500), (1800, 1200), (1200, 1800), (1600, 1400)]:
            new_w, new_l = update_elo(float(w), float(l))
            assert abs((new_w + new_l) - (w + l)) < 1e-10

    def test_custom_k_factor(self):
        """K factor scales the adjustment."""
        new_w_16, new_l_16 = update_elo(1500.0, 1500.0, k=16.0)
        new_w_32, new_l_32 = update_elo(1500.0, 1500.0, k=32.0)
        # K=16 gives half the adjustment of K=32 for equal ratings
        assert abs((new_w_16 - 1500.0) * 2 - (new_w_32 - 1500.0)) < 1e-10

    def test_winner_always_gains(self):
        """Winner always gains rating regardless of relative ratings."""
        for w, l in [(1500, 1500), (1800, 1200), (1200, 1800)]:
            new_w, new_l = update_elo(float(w), float(l))
            assert new_w > w
            assert new_l < l


class TestComputeSpecHash:
    def test_deterministic(self):
        """Same spec always produces the same hash."""
        assert compute_spec_hash("test spec") == compute_spec_hash("test spec")

    def test_different_specs_different_hashes(self):
        """Different specs produce different hashes."""
        assert compute_spec_hash("spec A") != compute_spec_hash("spec B")

    def test_length(self):
        """Hash is 16 hex characters."""
        h = compute_spec_hash("anything")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)
