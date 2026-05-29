# Region encoding

How regions are stored on disk + how to pick the right shape.

## Shape encoding

The `xy` field on a region metadata entry is an array whose first
element is the shape ID:

| Shape | xy encoding |
|-------|-------------|
| Point | `[1, x, y]` |
| Rectangle | `[2, x, y, width, height]` |
| Circle | `[3, cx, cy, r]` |
| Ellipse | `[4, cx, cy, rx, ry]` |
| Polyline | `[6, x1, y1, x2, y2, ...]` |
| Polygon | `[7, x1, y1, x2, y2, ...]` |

Coordinates are **image pixel space**, not browser canvas space.

Temporal annotations use the `z` field: `[t0, t1]` for a segment,
`[t]` for a single frame. For image annotations, `z` is always `[]`.

## Choose the natural shape first; rectangle is the fallback

Before placing each region, ask what the natural shape of the feature
is. Each shape choice is itself a claim about the feature, and a
varied-shape annotation set communicates more than a rectangle-only
one.

- Spheres, disc faces, clock faces, single visible eyes → **circle**
- Foreshortened domes, brims, oblique disks → **ellipse**
- Irregular tear-drops, triangular bodies, path/road footprints → **polygon**
- Thin diagonal features (struts, wires, ropes, branches) → **polyline**
- Directional intent (gaze, trajectory, line of sight) → **polyline**
  with arrow-phrase label
- Genuinely axis-aligned bounded regions (a window, a body, a building
  face) → **rectangle**

Default to rectangle only when the feature is genuinely rectangular or
when no other shape obviously fits.

## Coordinate space — prefer `xy_space="fraction"`

`via_add_region` and `via_update_region` accept an optional `xy_space`:

- `"fraction"` — coords are 0.0–1.0 fractions of the original dims;
  the server scales them for you. **Prefer this** — every multi-region
  session that used fractions has hit zero arithmetic errors. Read
  positions off the returned image proportionally ("about 40% from
  the left, 60% down") and pass the fractions through. Rect `w`/`h`
  and ellipse `rx`/`ry` scale per-axis; circle `r` scales by the
  geometric mean.
- `"original"` (default) — coords in `xy` are pixels in the *original*
  image. Kept as the default for back-compat; you have to do the
  returned-px-to-original-px multiplication yourself.

**If you must use `"original"`:**
Returned image 1024×683, original 4651×3101. A feature observed at
returned (500, 300) is at original
`(500 × 4651/1024, 300 × 3101/683)` ≈ `(2271, 1362)`. Multiply per
axis by `original_dim / returned_dim`. Or just use
`xy_space="fraction"` and pass `(500/1024, 300/683)` ≈
`(0.488, 0.439)` — same destination, no arithmetic.

## Shape choice for special cases

**Thin diagonal features** (struts, wires, ropes, poles, branches) want
the polyline encoding `[6, x1, y1, x2, y2]`, not an axis-aligned
rectangle. A bounding rect around a diagonal will be visibly off at
both ends; the polyline sits on the feature.

**Linear-but-wide features** (paths, stair flights, streams, road
surfaces) want the polygon footprint `[7, x1, y1, x2, y2, ...]`, not a
polyline along the centreline. A thin polyline beside an irregular
wide feature reads as "almost on it but not quite" even when
geometrically correct, because the eye cannot judge a 2-px line
against a 50-px-wide path. Trace the visible footprint as a polygon
instead.

**Directional / axial intent** (gaze, trajectory, approach line,
sightline) wants the polyline encoding `[6, x1, y1, x2, y2]` with an
arrow-phrase label (`fly→flower approach axis`, `looking toward X`).
An axis-aligned rectangle across the same line reads as a bounded
region, not a direction.

## Note on user-drawn regions

Browser-drawn regions occasionally arrive without a shape-ID prefix if
the browser saved an in-progress draw state — the first element will
be a large float rather than an integer 1–7. Treat these as polylines
(shape 6). Server-side repair runs on push (auto-restores the previous
shape when possible); for completeness, filter by `xy[0] <= 7` when
reading.

## Region ID conventions

- **Server-generated IDs** (from `via_add_region`): 8-char alphanumeric
  + `-_`, e.g. `8zFsb80J`. Never construct these; let the tool
  generate them.
- **Browser-drawn IDs**: `<vid>_<8char>`, e.g. `1_dOFsKKoJ`. The
  `<vid>_` prefix is added by VIA when the user draws directly in the
  browser. Both formats are valid and interchangeable for update /
  delete operations.

## File entry reference (for `via_update_project` hand-edits)

When hand-editing project JSON, each file entry looks like:

```json
{"fid": 1, "fname": "cat.jpg", "type": 2, "loc": 2,
 "src": "http://localhost:PORT/img/cat.jpg"}
```

**`type`** — media type. `2` = IMAGE (almost always this).

**`loc`** — where the file lives:
- `1` = LOCAL — file loaded from disk via the browser's file picker
  (no `src` used)
- `2` = URIHTTP — `src` is an HTTP URL; use this when `via_add_file`
  serves the image
- `3` = URIFILE — `src` is used verbatim as `img.src`
- `4` = INLINE — `src` is a data URI

**Always pair `loc=2` with an `http://localhost:PORT/img/<name>` `src`**
when adding files programmatically.

Each file entry must have a matching **view entry**:
```json
"view": {"1": {"fid_list": [1]}}
```

## Geometry helpers (manual recipes)

### Polyline → polygon band

Turn a polyline into a filled band polygon (e.g. road, track, river):

```python
import math

def polyline_to_band(points, half_width):
    """points = [(x,y), ...], returns flat polygon xy list [7, x1,y1, ...]"""
    n = len(points)
    perps = []
    for i in range(n):
        if i == 0:
            dx, dy = points[1][0]-points[0][0], points[1][1]-points[0][1]
        elif i == n-1:
            dx, dy = points[-1][0]-points[-2][0], points[-1][1]-points[-2][1]
        else:
            dx = points[i+1][0]-points[i-1][0]
            dy = points[i+1][1]-points[i-1][1]
        length = math.hypot(dx, dy) or 1
        perps.append((-dy/length, dx/length))
    left  = [(p[0] + half_width*n[0], p[1] + half_width*n[1])
             for p, n in zip(points, perps)]
    right = [(p[0] - half_width*n[0], p[1] - half_width*n[1])
             for p, n in zip(points, perps)]
    poly = left + list(reversed(right))
    return [7] + [c for pt in poly for c in pt]
```
