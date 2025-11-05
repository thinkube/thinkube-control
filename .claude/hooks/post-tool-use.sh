#!/bin/bash
# Post-Tool-Use Hook for Migration Reminders
# Reminds about documentation and validation after file changes

if [ "$TOOL_NAME" = "Write" ] || [ "$TOOL_NAME" = "Edit" ]; then
  echo "📝 Migration Reminders:"
  echo "  - Check COMPONENT_MAPPING.md for correct shadcn/ui component"
  echo "  - Apply DESIGN_PATTERNS.md criteria (Modal vs Inline, Card vs Table)"
  echo "  - Validate with /validate-migration skill before committing"
  echo "  - Use theme semantic colors (success, warning, destructive, info)"
fi
