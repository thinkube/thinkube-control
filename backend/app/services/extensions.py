# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: Apache-2.0

"""Extensions: parts of Thinkube Control that are built and deployed on their own.

An extension declares itself in its thinkube.yaml, under spec.extension:

    extension:
      title: Forge
      description: Fine-tune language models on your data
      icon: flame                    # a lucide icon name
      ui:
        module: /extension/index.js  # an ES module that exports mount(element, host)
        css: /extension/index.css
      nav:                           # entries in Control's navigation
        - label: Runs
          path: /
          icon: list-checks

Service discovery reads the declaration from the app's thinkube.yaml on its
Gitea main, so a pushed change is seen without a redeploy. Control draws the
extension's pages inside its own layout: it loads the module through
/api/v1/x/<name>/ui/ and forwards the module's API calls through
/api/v1/x/<name>/api/ to the extension's /api/v1/, with the user's token.
"""

from typing import Any, Dict, Optional, Tuple


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be text that is not empty")
    return value.strip()


def _path(value: Any, field: str) -> str:
    text = _text(value, field)
    if not text.startswith("/") or ".." in text:
        raise ValueError(f"{field} must be a path that starts with / and has no ..")
    return text


def check_extension(declared: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """The extension declaration as Control uses it, or the reason it is refused."""
    try:
        if not isinstance(declared, dict):
            raise ValueError("spec.extension must be a mapping")
        ui = declared.get("ui")
        if not isinstance(ui, dict):
            raise ValueError("spec.extension.ui must be a mapping with module")
        module = _path(ui.get("module"), "spec.extension.ui.module")
        css = _path(ui["css"], "spec.extension.ui.css") if "css" in ui else None
        folder = module.rsplit("/", 1)[0] + "/"
        if css is not None and not css.startswith(folder):
            raise ValueError(f"spec.extension.ui.css must be in the module's folder {folder}")
        nav = declared.get("nav")
        if not isinstance(nav, list) or not nav:
            raise ValueError("spec.extension.nav must list at least one entry")
        entries = []
        for i, entry in enumerate(nav):
            if not isinstance(entry, dict):
                raise ValueError(f"spec.extension.nav[{i}] must be a mapping")
            entries.append({
                "label": _text(entry.get("label"), f"spec.extension.nav[{i}].label"),
                "path": _path(entry.get("path"), f"spec.extension.nav[{i}].path"),
                "icon": _text(entry["icon"], f"spec.extension.nav[{i}].icon") if "icon" in entry else None,
            })
        return {
            "title": _text(declared.get("title"), "spec.extension.title"),
            "description": declared.get("description") or "",
            "icon": _text(declared["icon"], "spec.extension.icon") if "icon" in declared else None,
            "ui": {"module": module, "css": css, "folder": folder},
            "nav": entries,
        }, None
    except ValueError as e:
        return None, str(e)
