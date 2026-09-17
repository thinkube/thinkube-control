"""Where a deploy's checkout lives, and which deployed commits a template deploy would replace.

An app the user works on lives under apps/, a platform component under
components/. The deploy request says which, from the template catalog, so the
checkout is in its place before anything is pulled or rendered; a
thinkube.yaml that declares otherwise stops the deploy.

A template deploy commits "Deploy <name> to <domain>" and every build commits
"build: automatic update of <name> to <image>". Any other commit after the
last deploy commit is a change a developer made, pushed and had built: it is
running. Deploying the template again replaces it, so the deploy names those
commits and stops unless the replacement was confirmed, and a confirmed
replacement keeps them on a branch of the repository first.
"""

from __future__ import annotations

from dataclasses import dataclass

DEPLOYMENT_TYPES = ("component", "user_app")


@dataclass(frozen=True)
class Commit:
    sha: str
    author: str
    subject: str


def checkout_path(apps_dir: str, components_dir: str, app_name: str, deployment_type: str) -> str:
    """The checkout of this deploy: components/<name> for a component, apps/<name> for an app."""
    if deployment_type not in DEPLOYMENT_TYPES:
        raise ValueError(
            f"deployment_type {deployment_type!r} is not one of {DEPLOYMENT_TYPES}; "
            f"the deploy request sets it from the template catalog"
        )
    return f"{components_dir if deployment_type == 'component' else apps_dir}/{app_name}"


def check_declared_type(deployment_type: str, declares_component: bool, app_name: str) -> None:
    """Refuse a template whose thinkube.yaml disagrees with the catalog about being a component."""
    if declares_component and deployment_type != "component":
        raise ValueError(
            f"thinkube.yaml of {app_name} declares a component, but the template catalog does not list it as one; "
            f"add it to the catalog as a component or change its deployment type"
        )
    if not declares_component and deployment_type == "component":
        raise ValueError(
            f"the template catalog lists {app_name} as a component, but its thinkube.yaml does not declare "
            f"deployment type component"
        )


def parse_log(output: str) -> list[Commit]:
    """Commits from `git log --format=%H%x09%an%x09%s`, newest first."""
    commits = []
    for line in output.splitlines():
        if not line.strip():
            continue
        sha, author, subject = line.split("\t", 2)
        commits.append(Commit(sha, author, subject))
    return commits


def developer_commits(log: list[Commit], app_name: str, domain: str) -> list[Commit]:
    """The commits after the last template deploy that neither a deploy nor a build wrote, newest first."""
    deploy = f"Deploy {app_name} to {domain}"
    build = f"build: automatic update of {app_name} to "
    out = []
    for commit in log:
        if commit.subject == deploy:
            break
        if not commit.subject.startswith(build):
            out.append(commit)
    return out


def refusal(app_name: str, commits: list[Commit]) -> str:
    """Why the deploy stops, the commits it would replace, and how to go ahead."""
    listed = "\n".join(f"  {c.sha[:10]} {c.author}: {c.subject}" for c in commits)
    return (
        f"{app_name} runs {len(commits)} commit(s) pushed after its last template deploy, and deploying the "
        f"template again replaces them:\n{listed}\n"
        f"To replace them, deploy again with the variable _replace_developer_commits set to true; "
        f"the commits are kept on a branch of the repository first."
    )
