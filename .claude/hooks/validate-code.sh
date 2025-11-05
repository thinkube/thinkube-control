#!/bin/bash
# PostToolUse Hook for React/thinkube-style validation
# Validates proper component usage after Edit/Write operations

# Read JSON input from stdin
INPUT=$(cat)

# Extract the file path that was edited
FILE_PATH=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_response.filePath // ""')

# Only validate .tsx, .ts, .jsx files in frontend
if [[ ! "$FILE_PATH" =~ \.(tsx|ts|jsx)$ ]] || [[ ! "$FILE_PATH" =~ frontend/ ]]; then
  exit 0  # Not a frontend file, skip validation
fi

VIOLATIONS=""

# Check 1: Inline styled elements
if grep -E 'className="[^"]*\s+(bg-|p-[0-9]|rounded|border)[^"]*"' "$FILE_PATH" | grep -v "// @allowed-inline" | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• Inline styled elements found (use TkCard, TkButton from thinkube-style)\n"
fi

# Check 2: Raw button elements
if grep '<button' "$FILE_PATH" | grep -v 'TkButton' | grep -v '// @allowed-button' | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• Raw <button> elements found (use TkButton from thinkube-style)\n"
fi

# Check 3: TODO/FIXME/PLACEHOLDER
if grep -E 'TODO|FIXME|PLACEHOLDER' "$FILE_PATH" | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• TODO/FIXME/PLACEHOLDER found (per MIGRATION_COMPLETENESS_RULES.md: NO placeholders!)\n"
fi

# Check 4: DaisyUI classes
if grep -E 'class.*\(btn\|card\|modal\|badge\|alert\|drawer\|menu\|navbar\|footer\|hero\|stats\|steps\)' "$FILE_PATH" | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• DaisyUI classes found (use shadcn/ui equivalents)\n"
fi

# Check 5: Vue syntax
if grep -E 'v-if|v-for|v-model|v-show|@click|:class' "$FILE_PATH" | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• Vue syntax found (must use React)\n"
fi

# Check 6: Pinia imports
if grep -E "from ['\"]pinia['\"]" "$FILE_PATH" | grep -q .; then
  VIOLATIONS="${VIOLATIONS}• Pinia imports found (use Zustand)\n"
fi

# If violations found, block with structured JSON
if [ -n "$VIOLATIONS" ]; then
  cat <<EOF
{
  "decision": "block",
  "reason": "Code quality violations in $FILE_PATH:\n$VIOLATIONS\nFix these before continuing."
}
EOF
  exit 2  # Exit code 2 = blocking error
fi

# No violations, allow
exit 0
