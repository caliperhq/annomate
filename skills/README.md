# Claude Code Skills

This skill teaches Claude how to work with the VIA image annotator and the annotate MCP server.

## Skills included

| Skill | Description |
|-------|-------------|
| `annotate` | Core workflow: read annotations → discuss → add/edit regions → auto-sync to browser. Required for using the MCP server effectively. |

## Installation

```bash
SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
cp -r skills/annotate "$SKILLS_DIR/"
```

After copying, the skill activates automatically when Claude detects annotation work.
