#!/usr/bin/env python3
"""
Patch VIA image annotator HTML from the submodule and copy to src/annotate/.

Run before building a release:
    python scripts/build_html.py

The submodule must be initialised and the VIA dist must be built first:
    git submodule update --init --recursive
    cd via/via-3.x.y/scripts && python3 pack.py via image_annotator && cd ../../..
"""

from pathlib import Path

ROOT = Path(__file__).parent.parent
VIA_HTML_SRC = ROOT / "via" / "via-3.x.y" / "dist" / "via_image_annotator.html"
VIA_HTML_DST = ROOT / "src" / "annotate" / "via_image_annotator.html"

# If VIA bumps its major version directory (e.g. via-4.x.y), update this:
VIA_VERSION_DIR = "via-3.x.y"

REMOTE_STORE_LINE = "const _VIA_REMOTE_STORE = 'https://zeus.robots.ox.ac.uk/via/store/3.x.y/';"
PATCHED_STORE_LINE = "const _VIA_REMOTE_STORE = 'http://localhost:__VIA_MCP_PORT__/';"

# Upstream VIA bug: `current_vid in vid_list` uses JS `in` (index check) on an array,
# so on every project update the browser falls back to vid_list[0] unless the user
# happens to be on a vid whose value matches a valid array index. Fix to .indexOf.
VIEW_RESET_BUG = "if ( current_vid in this.d.store.project.vid_list ) {"
VIEW_RESET_FIX = "if ( this.d.store.project.vid_list.indexOf(current_vid) !== -1 ) {"

AUTO_POLL_SNIPPET = """\
<script>
/* annotate: incremental pull — avoid view_show reset on metadata-only changes.
   Replaces the naive via.s.pull(pid), which triggers project_loaded →
   view_show(vid_list[0]), bouncing the user back to the first file and
   wiping in-progress UI state on every server write. */
(function() {
  var polling = false;
  setInterval(async function() {
    if (polling) return;
    polling = true;
    try {
      var pid = via.d.store.project.pid;
      if (!pid || pid === '__VIA_PROJECT_ID__') return;
      var r = await fetch('http://localhost:__VIA_MCP_PORT__/' + pid);
      if (!r.ok) return;
      var remote = await r.json();
      var local = via.d.store;
      if (remote.project.rev === local.project.rev) return;

      // Structural change (files/views/attributes/config)? Fall back to full pull.
      var structural = ['file', 'view', 'attribute', 'config'];
      for (var i = 0; i < structural.length; i++) {
        var k = structural[i];
        if (JSON.stringify(local[k]) !== JSON.stringify(remote[k])) {
          via.s.pull(pid);
          return;
        }
      }

      // Metadata-only diff.
      var oldMeta = local.metadata, newMeta = remote.metadata;
      var added = [], updated = [], removedByVid = {};
      for (var mid in newMeta) {
        if (!(mid in oldMeta)) added.push(mid);
        else if (JSON.stringify(oldMeta[mid]) !== JSON.stringify(newMeta[mid]))
          updated.push(mid);
      }
      for (var mid in oldMeta) {
        if (!(mid in newMeta)) {
          var vid = oldMeta[mid].vid;
          (removedByVid[vid] = removedByVid[vid] || []).push(mid);
        }
      }

      // Patch store + reset merge baseline (mirror what via.s.pull does).
      local.metadata = newMeta;
      local.project.rev = remote.project.rev;
      local.project.rev_timestamp = remote.project.rev_timestamp;
      via.d.store0 = JSON.parse(JSON.stringify(local));

      // Synthesize per-region events — file_annotator handles each
      // incrementally (_creg_add + _creg_draw_all), no view re-init.
      for (var i = 0; i < added.length; i++) {
        var mid = added[i], vid = newMeta[mid].vid;
        if (vid && local.view[vid])
          via.d.emit_event('metadata_add', { vid: vid, mid: mid });
      }
      for (var i = 0; i < updated.length; i++) {
        var mid = updated[i], vid = newMeta[mid].vid;
        if (vid && local.view[vid])
          via.d.emit_event('metadata_update', { vid: vid, mid: mid });
      }
      for (var vid in removedByVid)
        via.d.emit_event('metadata_delete_bulk',
                         { vid: vid, mid_list: removedByVid[vid] });
    } catch(e) { /* swallow — next tick retries */ }
    finally { polling = false; }
  }, 3000);
})();
</script>
"""

# project_loaded handler also resets to vid_list[0] unconditionally; patch it
# to preserve current_vid when it's still present (covers the structural-change
# fallback path through via.s.pull).
PROJECT_LOADED_BUG = """_via_view_manager.prototype._on_event_project_loaded = function(data, event_payload) {
  this._init_ui_elements();
  this._view_selector_update();
  if ( this.d.store.project.vid_list.length ) {
    // show first view by default
    this.va.view_show( this.d.store.project.vid_list[0] );
  }
}"""

PROJECT_LOADED_FIX = """_via_view_manager.prototype._on_event_project_loaded = function(data, event_payload) {
  var current_vid = this.va.vid;
  this._init_ui_elements();
  this._view_selector_update();
  if ( this.d.store.project.vid_list.length ) {
    if ( current_vid && this.d.store.project.vid_list.indexOf(current_vid) !== -1 ) {
      this.va.view_show( current_vid );
    } else {
      // show first view by default
      this.va.view_show( this.d.store.project.vid_list[0] );
    }
  }
}"""


def main():
    if not VIA_HTML_SRC.exists():
        via_dist_dir = VIA_HTML_SRC.parent
        if not via_dist_dir.exists():
            raise FileNotFoundError(
                f"{VIA_HTML_SRC} not found.\n"
                "1. Initialize the submodule:  git submodule update --init --recursive\n"
                f"2. Build VIA dist:            cd via/{VIA_VERSION_DIR}/scripts && python3 pack.py via image_annotator"
            )
        raise FileNotFoundError(
            f"{VIA_HTML_SRC} not found. Build it first:\n"
            f"  cd via/{VIA_VERSION_DIR}/scripts && python3 pack.py via image_annotator"
        )

    html = VIA_HTML_SRC.read_text(encoding="utf-8")

    if REMOTE_STORE_LINE not in html:
        raise ValueError(
            f"Expected line not found in VIA HTML:\n  {REMOTE_STORE_LINE}\n"
            f"VIA may have changed. Update VIA_VERSION_DIR or REMOTE_STORE_LINE in this script."
        )

    html = html.replace(REMOTE_STORE_LINE, PATCHED_STORE_LINE)

    if VIEW_RESET_BUG not in html:
        raise ValueError(
            f"Expected view-reset bug line not found in VIA HTML:\n  {VIEW_RESET_BUG}\n"
            f"Upstream may have fixed it (check) or moved it; update VIEW_RESET_BUG in this script."
        )
    html = html.replace(VIEW_RESET_BUG, VIEW_RESET_FIX)

    if PROJECT_LOADED_BUG not in html:
        raise ValueError(
            "Expected project_loaded handler not found in VIA HTML; "
            "upstream may have moved it. Update PROJECT_LOADED_BUG in this script."
        )
    html = html.replace(PROJECT_LOADED_BUG, PROJECT_LOADED_FIX)

    html = html.replace("</body>", AUTO_POLL_SNIPPET + "</body>", 1)

    VIA_HTML_DST.parent.mkdir(parents=True, exist_ok=True)
    VIA_HTML_DST.write_text(html, encoding="utf-8")
    print(f"Written: {VIA_HTML_DST}")


if __name__ == "__main__":
    main()
