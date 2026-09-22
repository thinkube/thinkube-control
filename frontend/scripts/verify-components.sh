#!/bin/bash

# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

# Verify no custom styled components in app/

echo "🔍 Checking for custom component violations..."

VIOLATIONS=0

# Check for inline className styling patterns (anti-pattern)
echo "Checking for inline styled divs/buttons..."
INLINE_STYLES=$(grep -rn "className=\".*bg-\|className=\".*p-[0-9]\|className=\".*rounded\|className=\".*border" app/ --include="*.tsx" --include="*.ts" | grep -v "// allowed" | grep -v node_modules || true)

if [ -n "$INLINE_STYLES" ]; then
  echo "❌ Found inline styling (should use Tk components):"
  echo "$INLINE_STYLES"
  VIOLATIONS=$((VIOLATIONS + 1))
fi

# Check for raw buttons (should use TkButton)
echo "Checking for raw <button> elements..."
RAW_BUTTONS=$(grep -rn "<button" app/ --include="*.tsx" | grep -v "TkButton" | grep -v node_modules || true)

if [ -n "$RAW_BUTTONS" ]; then
  echo "❌ Found raw <button> elements (should use TkButton):"
  echo "$RAW_BUTTONS"
  VIOLATIONS=$((VIOLATIONS + 1))
fi

# Check for custom component definitions in app/ or components/
echo "Checking for component definitions in app/ or components/..."
CUSTOM_COMPONENTS=$(grep -rn "^export.*function.*Component\|^const.*=.*=>.*{$" app/ components/ --include="*.tsx" 2>/dev/null | grep -v "Page\|Content\|Provider" | grep -v node_modules || true)

if [ -n "$CUSTOM_COMPONENTS" ]; then
  echo "⚠️  Found potential custom components (verify they're not visual components):"
  echo "$CUSTOM_COMPONENTS"
fi

if [ $VIOLATIONS -gt 0 ]; then
  echo ""
  echo "❌ Component verification FAILED with $VIOLATIONS violation(s)"
  echo "Fix these issues before deploying:"
  echo "  - Use TkCard instead of <div className='bg-card ...'>"
  echo "  - Use TkButton instead of <button>"
  echo "  - Use Tk components from thinkube-style for all visual elements"
  exit 1
fi

echo "✅ Component verification passed"
exit 0
