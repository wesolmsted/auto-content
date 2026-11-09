# Project skills

Each skill lives in its own folder as `.claude/skills/<skill-name>/SKILL.md`. Claude Code loads it automatically for this project.

```markdown
---
name: skill-name
description: When to use this skill: specific triggers, so it loads at the right time.
---

Step-by-step instructions for the workflow.
```

Add a skill when a multi-step project procedure is done more than once (e.g. adding a recipe variable end to end, the render dev loop, wiring a new platform adapter). Skills must follow the test-first workflow in the root CLAUDE.md.
