"""Tiling helpers — auto-grid, generate_tiles, nms_merge, filter_existing.

No torch / transformers needed; these are pure-Python.
"""

from __future__ import annotations

import pytest

from annomate.models.tiling import (
    CandidateBox,
    DEFAULT_BROAD_PROMPTS,
    auto_grid,
    filter_existing,
    generate_tiles,
    grid_to_tile_count,
    iou,
    nms_merge,
    parse_grid,
)


# --- auto_grid heuristic ---

@pytest.mark.parametrize("w,h,expected", [
    (640, 480,   "none"),     # tiny
    (1024, 1024, "none"),     # boundary
    (1500, 1000, "2x2"),
    (2048, 2048, "2x2"),
    (3000, 2000, "4x4"),
    (4096, 4096, "4x4"),
    (5000, 5000, "8x8"),
    (16000, 12000, "16x16"),
])
def test_auto_grid_buckets(w, h, expected):
    assert auto_grid(w, h) == expected


def test_grid_to_tile_count():
    assert grid_to_tile_count("none") == 1
    assert grid_to_tile_count("2x2") == 4
    assert grid_to_tile_count("4x4") == 16
    assert grid_to_tile_count("8x8") == 64


def test_parse_grid():
    assert parse_grid("none") == (1, 1)
    assert parse_grid("4x4") == (4, 4)


# --- generate_tiles ---

def test_generate_tiles_none_returns_full_image():
    tiles = generate_tiles(1000, 800, "none")
    assert len(tiles) == 1
    assert tiles[0].coords == (0, 0, 1000, 800)


def test_generate_tiles_2x2_covers_image_with_overlap():
    tiles = generate_tiles(1000, 800, "2x2", overlap=0.2)
    assert len(tiles) == 4
    # All tiles must be within image bounds
    for t in tiles:
        assert t.x >= 0 and t.y >= 0
        assert t.x + t.w <= 1000
        assert t.y + t.h <= 800
    # The four-corner tiles should each cover their own quadrant
    quadrants = {(t.col, t.row) for t in tiles}
    assert quadrants == {(0, 0), (1, 0), (0, 1), (1, 1)}


def test_generate_tiles_4x4_yields_sixteen_tiles():
    tiles = generate_tiles(4000, 2000, "4x4")
    assert len(tiles) == 16


def test_generate_tiles_auto_picks_by_image_size():
    tiles = generate_tiles(800, 600, "auto")
    assert len(tiles) == 1  # auto → none for small images
    tiles = generate_tiles(3000, 2000, "auto")
    assert len(tiles) == 16  # auto → 4x4


def test_generate_tiles_overlap_means_adjacent_tiles_share_pixels():
    tiles = generate_tiles(1000, 1000, "2x2", overlap=0.2)
    tile00 = next(t for t in tiles if (t.col, t.row) == (0, 0))
    tile10 = next(t for t in tiles if (t.col, t.row) == (1, 0))
    # tile00 ends past where tile10 starts → overlap
    assert tile00.x + tile00.w > tile10.x


# --- IoU & NMS ---

def test_iou_basics():
    a = CandidateBox(0, 0, 0.5, 0.5, "p", 0.9)
    b = CandidateBox(0, 0, 0.5, 0.5, "p", 0.8)
    assert iou(a, b) == pytest.approx(1.0)
    c = CandidateBox(0.6, 0.6, 0.2, 0.2, "p", 0.7)
    assert iou(a, c) == 0.0
    # Partial overlap
    d = CandidateBox(0.25, 0.25, 0.5, 0.5, "p", 0.6)
    val = iou(a, d)
    assert 0.0 < val < 1.0


def test_nms_merge_drops_duplicates_at_threshold():
    cands = [
        CandidateBox(0.1, 0.1, 0.2, 0.2, "cat", 0.9),
        CandidateBox(0.11, 0.11, 0.2, 0.2, "cat", 0.7),   # near-duplicate
        CandidateBox(0.5, 0.5, 0.2, 0.2, "cat", 0.8),     # distinct
    ]
    merged = nms_merge(cands, iou_threshold=0.5)
    assert len(merged) == 2
    scores = sorted(c.score for c in merged)
    assert scores == [0.8, 0.9]


def test_nms_merge_per_label_keeps_overlapping_different_labels():
    cands = [
        CandidateBox(0.1, 0.1, 0.5, 0.5, "cat", 0.9),
        CandidateBox(0.1, 0.1, 0.5, 0.5, "dog", 0.8),
    ]
    merged = nms_merge(cands, iou_threshold=0.5, per_label=True)
    assert len(merged) == 2
    merged_global = nms_merge(cands, iou_threshold=0.5, per_label=False)
    assert len(merged_global) == 1


# --- filter_existing ---

def test_filter_existing_drops_overlapping_candidates():
    cands = [
        CandidateBox(0.10, 0.10, 0.20, 0.20, "person", 0.9),  # near (0.1,0.1,0.2,0.2) existing
        CandidateBox(0.60, 0.60, 0.20, 0.20, "person", 0.8),  # standalone
    ]
    existing = [(0.10, 0.10, 0.20, 0.20)]
    out = filter_existing(cands, existing, iou_threshold=0.5)
    assert len(out) == 1
    assert out[0].x == pytest.approx(0.60)


def test_filter_existing_with_no_existing_keeps_all():
    cands = [CandidateBox(0.1, 0.1, 0.2, 0.2, "p", 0.9)]
    assert len(filter_existing(cands, [])) == 1


# --- broad prompts default ---

def test_default_broad_prompts_nonempty_and_contains_common_classes():
    assert "person" in DEFAULT_BROAD_PROMPTS
    assert len(DEFAULT_BROAD_PROMPTS) >= 8
