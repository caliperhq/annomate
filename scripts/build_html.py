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

AUTO_POLL_SNIPPET = """\
<script>
/* via-mcp: auto-pull when Claude writes new annotations */
setInterval(async () => {
  const pid = via.d.store.project.pid;
  if (!pid || pid === '__VIA_PROJECT_ID__') return;
  try {
    const r = await fetch('http://localhost:__VIA_MCP_PORT__/' + pid);
    if (!r.ok) return;
    const remote = await r.json();
    if (remote.project.rev !== via.d.store.project.rev)
      via.s.pull(pid);
  } catch(e) {}
}, 3000);
</script>
"""


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
    html = html.replace("</body>", AUTO_POLL_SNIPPET + "</body>", 1)

    VIA_HTML_DST.parent.mkdir(parents=True, exist_ok=True)
    VIA_HTML_DST.write_text(html, encoding="utf-8")
    print(f"Written: {VIA_HTML_DST}")


if __name__ == "__main__":
    main()
