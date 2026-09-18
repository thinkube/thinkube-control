"""What a template deploy would destroy in the checkout the developer works in, and what it replaces there.

A template deploy resets the checkout (apps/<name> or components/<name>) to
the Gitea repository's main with `git reset --hard origin/main`, copies the
template over it with `copier copy --force`, and regenerates k8s/. Changes to
tracked files that are not committed, and commits that are not pushed, exist
only in that checkout: the reset destroys them. The deploy therefore stops
when the checkout holds either, names them, and says to commit and push first.
"""

from __future__ import annotations

from typing import Optional

from component_checkout import Commit

STATUS_COMMAND = "git status --porcelain --untracked-files=no"
UNPUSHED_COMMAND = "git log --format='%H%x09%an%x09%s' origin/main..HEAD"


def changed_files(porcelain: str) -> list[str]:
    """The tracked files `git status --porcelain` reports as changed, a rename as `old -> new`."""
    return [line[3:] for line in porcelain.splitlines() if line.strip()]


def refusal(app_name: str, checkout: str, changed: list[str], unpushed: list[Commit]) -> Optional[str]:
    """Why the deploy stops before resetting the checkout, or None when the checkout holds nothing of its own."""
    if not changed and not unpushed:
        return None
    parts = [
        f"The checkout {checkout} holds work that is not in Gitea, and a deploy of {app_name} resets it "
        f"to Gitea's main with git reset --hard, which destroys that work:"
    ]
    if changed:
        parts.append(f"{len(changed)} changed file(s) not committed:")
        parts.extend(f"  {path}" for path in changed)
    if unpushed:
        parts.append(f"{len(unpushed)} commit(s) not pushed to Gitea:")
        parts.extend(f"  {c.sha[:10]} {c.author}: {c.subject}" for c in unpushed)
    parts.append(
        f"Commit and push them first (cd {checkout} && git add <files> && git commit && git pull --rebase "
        f"&& git push). A push to Gitea builds and deploys the app by itself; redeploy only to apply a "
        f"changed thinkube.yaml or to render the template again."
    )
    return "\n".join(parts)


def replacement_warning(checkout: str, checkout_exists: bool) -> Optional[str]:
    """What a deploy replaces in an existing checkout, or None when there is no checkout to replace."""
    if not checkout_exists:
        return None
    return (
        f"The deploy resets the checkout {checkout} to Gitea's main (git reset --hard; it refuses when the "
        f"checkout has uncommitted changes or unpushed commits), copies the template's files over it "
        f"(copier copy --force), regenerates k8s/ from thinkube.yaml, and commits and pushes the result. "
        f"An existing database of the app is kept."
    )
