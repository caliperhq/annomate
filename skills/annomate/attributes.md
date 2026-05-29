# Attribute schema

How label/description fields are stored, what makes a good description,
and the bulk-edit shape.

## Default schema

Every new project starts with two TEXT attributes pre-installed:

```json
"attribute": {
  "1": {"aname": "label",       "anchor_id": "FILE1_Z0_XY1", "type": 1, ...},
  "2": {"aname": "description", "anchor_id": "FILE1_Z0_XY1", "type": 1, ...}
}
```

`config.ui.spatial_region_label_attribute_id = "1"` makes VIA render the
`label` value on the canvas next to each region.

**`av` key conventions:** keys are attribute IDs as strings (`"1"`,
`"2"`), values are always strings. Empty annotation: `av: {}`.

> **The av-key gotcha.** The default schema's attributes are `"1"`
> (label) and `"2"` (description). Pass *those IDs* as keys —
> `av: {"1": "jaguar", "2": "head-on pose"}`. The server also accepts
> the human anames as a courtesy:
> `av: {"label": "jaguar", "description": "..."}` gets remapped to
> numeric IDs. **Unknown keys are now rejected** (they used to be
> silently stored, which dropped labels from the canvas). If you see
> `Unknown av key(s)`, call `via_get_project` to see the current schema.

## Attribute conventions

**`label` names the thing. `description` cites the visible evidence**
for the label — colour, shape, scale, texture, position — so the
annotation is a falsifiable claim, not a free-text guess. Compare:

- Weak: `label="compound eye"`, `description="red eye"`
- Strong: `label="compound eye"`,
  `description="deep red-brown holoptic eye with visible pseudopupil
  and fine pale setae on the surface"`

The strong form lets the next reader (or the user) check the claim
against the pixels; the weak form is just a re-statement of the label.

**For labels that imply a measurable property** (grain size, age class,
magnitude, count, distance, height), ground the term in the visible
scale of the feature, not the lexical association. Example: pebble
(4–64 mm) vs cobble (64–256 mm) vs boulder (>256 mm) are real
Wentworth distinctions — don't reach for "pebble" just because the
rocks are rounded and in a streambed. Same family: "small mammal"
without a length cue, "tall tree" without a scale reference,
"magnitude 5 quake" with no felt-intensity evidence.

**Describe what's visible, not what it might be called.** For terms
imported from training data (architectural names, species,
archaeological labels, geological formations), describe the visible
structure first and supply the canonical name only if the image
itself disambiguates. Resisting "Court of Three Stupas" in favour of
"upper terrace ruins (north annex)" is a feature, not a hedge — the
visible description is verifiable; the canonical name needs a site
plan to confirm.

## Attribute object shape (for `via_update_project` bulk edits)

```json
{
  "aname": "label",
  "anchor_id": "FILE1_Z0_XY1",
  "type": 1,
  "desc": "Short description shown in UI",
  "options": {},
  "default_option_id": ""
}
```

**`anchor_id` values:**
- `"FILE1_Z0_XY1"` — spatial region attribute (bounding boxes,
  polygons, etc.)
- `"FILE1_Z0_XY0"` — file-level attribute (applies to the whole image)

**`type` enum:**
- `1` = TEXT
- `2` = CHECKBOX
- `3` = RADIO
- `4` = SELECT
- `5` = IMAGE

For SELECT/RADIO/CHECKBOX, populate `options` as
`{"0": "Cat", "1": "Dog", ...}`.

## Making labels visible on the canvas

`config.ui.spatial_region_label_attribute_id` must be set to the
attribute ID (as a string) whose value you want rendered on each
region. It defaults to `"1"` (the `label` attribute). If labels stop
appearing, the browser UI may have reset it — re-assert it with a
`via_update_project` call that sets it back to `"1"`.
