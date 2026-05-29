# Perception gotchas

Errors that have shown up repeatedly across sessions and survived the
overlay check. Each one costs a user-correction round if you don't
watch for it.

## Box geometry

**Extend the box to the visible extremity, not the bulk.** When boxing
a shape with a thin, curved, or projecting part — a tail, an abdomen
tip, an ear, a wing tip, the south wing of an L-shaped roof — the
reflexive failure is to box the *bulk* of the feature and clip the
*extremity*. Before declaring a placement done, identify the
lowest/rightmost/etc. pixel that semantically belongs to the named
feature and check the box edge reaches it.

**Box the whole named object, not its most prominent part.** A box
labelled `church` covers nave + tower + spire, not just the tower. A
box labelled `foot warmer` covers the wooden housing + base + visible
shadow, not just the pierced-top cube. A box labelled `tree` covers
trunk + crown together. Different family from extend-to-extremity
(which is about a single part's edges) — this is about *which
structural parts count as the thing being named*. Ask: if a viewer
drew a line around "the X" with no prior context, where would they
stop? Box to there.

## Coordinate placement

**Re-crop immediately before placing small features.** Crops you took
during orientation fall out of working memory after ~5 unrelated tool
calls. For any feature under ~10% of canvas extent, call
`via_get_image_crop` on its local area again *just before* placing —
not as a correction step after. Reading position off a remembered
orientation crop reverts to thumbnail-eyeball precision and produces
30–500 px offsets that survive overlay. Sub-30 px features in clean
backgrounds (single birds in sky, distant figures) want this even
more strongly — place coords directly off a full-res crop, not off
the full-image overlay.

**Densely-figured vertical compositions: pre-crop the halves.** When a
figural composition fills most of the frame vertically (a Lamentation,
a multi-figure interior, a tall street scene), a single full-image
overlay at 2048 px compresses the vertical extent enough to hide ~15%
y-bias on your placements. Before placing any boxes, take two crops —
upper half and lower half — and place positions off those, not the
full-image view. Saves a full correction round.

## Identity and labelling

**Crop-and-drop, not just crop-and-place.** `via_get_image_crop` is
also a go/no-go tool. If a candidate feature doesn't survive a
high-resolution crop — the "lightning tower" turns out to be utility
poles, the "single wall clock" turns out to be empty shelf-front —
drop the annotation rather than place a confident box on a feature
you can't actually see. Resist the urge to label features from prior
expectation when the pixels don't disambiguate them.

**Verify identity, not just position.** For named-part labels
(windscreen, lens, helmet, eye, cockpit), ask "does this shape
actually *look like* the thing I'm calling it?" — a "windscreen"
should be visibly transparent; a "helmet" should be solid; an "eye"
should have a pupil. The overlay check confirms a box is in the right
neighborhood; it does *not* catch you labeling the helmeted head
"windscreen" because the prior expected one nearby. One session
shipped a complete pilot↔windscreen label swap that overlay passed.

**Reflections are separate regions.** On still water, glass, polished
floors, or any mirror surface, the reflection is its own visual
region — not part of the object it reflects. A box for "tree" must
not extend into the inverted mirror of the tree below the waterline,
even though the two look visually identical. If the image has a
reflective surface, add a separate region for the reflection rather
than letting an object box swallow it.

## Priors that lie

**Time-of-day priors need season and latitude.** "5 a.m. local" is
pre-dawn in winter at mid-latitudes but well past sunrise in late
spring; "morning" at the equator is different from the same hour at
60° N. When your prior depends on lighting, shadow direction, or sky
colour from a timestamp, check season + latitude before committing —
otherwise the clock-face overrides the actual photometry visible in
the image. **Call `via_read_metadata(fid)` at session start** — it
returns capture timestamp + GPS in one call, which is enough to fix
the priors before you place anything.

## Verification discipline

**Overlay is necessary but not sufficient.** The overlay renders at
returned-image resolution; you're judging placements at coarser
spatial fidelity than the user sees in the browser at full resolution.
Before declaring done / saving / handing off, pause and let the user
spot-check in the browser. Most errors that survive overlay are
caught by the user in ~5 seconds.

**Use `via_get_image_crop` when overlay is ambiguous.** If a placement
looks "close" on overlay but you're not sure, call
`via_get_image_crop(fid, bbox)` on the area at full resolution. The
crop is taken from the original image, so a 500×500 window inside a
4651×3101 photo comes back at native pixel density, not the heavily-
downscaled view.
