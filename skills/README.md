# Claude Code Skills

This skill teaches Claude how to work with the VIA image annotator and the annomate MCP server.

## Skills included

| Skill | Description |
|-------|-------------|
| `annomate` | Core workflow: read annotations → discuss → add/edit regions → auto-sync to browser. Required for using the MCP server effectively. |

## Installation

```bash
SKILLS_DIR="$HOME/.claude/skills"
mkdir -p "$SKILLS_DIR"
cp -r skills/annomate "$SKILLS_DIR/"
```

After copying, the skill activates automatically when Claude detects annotation work.
