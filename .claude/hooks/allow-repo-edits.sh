#!/usr/bin/env bash
# PermissionRequest hook for Write/Edit/NotebookEdit: auto-approve edits to files
# inside this repo. Anything else (outside the repo, paths with "..", or this
# repo's Claude settings files) falls through to the normal permission prompt.

input=$(cat)
file=$(printf '%s' "$input" | jq -r '.tool_input.file_path // .tool_input.notebook_path // ""')
repo="${CLAUDE_PROJECT_DIR:-}"

[ -n "$file" ] && [ -n "$repo" ] || exit 0

case "$file" in
  *"/../"* | *"/.." | *"/./"*) exit 0 ;;
  "$repo"/.claude/settings*.json) exit 0 ;;
  "$repo"/*)
    jq -n '{hookSpecificOutput: {hookEventName: "PermissionRequest", decision: {behavior: "allow"}}}'
    ;;
esac
