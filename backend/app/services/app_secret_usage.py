"""Which applications use which secrets from the Secrets store.

Both deploy paths record usage here: regeneration calls record_usage directly,
the first deploy through the track-usage route, which calls it too.
"""

from typing import List

from sqlalchemy.orm import Session

from app.models.secrets import AppSecret, Secret


class UnknownSecret(LookupError):
    """A secret named for an application is not in the Secrets store."""


def record_usage(db: Session, app_name: str, secret_names: List[str]) -> None:
    """Replace the application's recorded usage with exactly these secrets."""
    secrets = (
        db.query(Secret).filter(Secret.name.in_(secret_names)).all() if secret_names else []
    )
    missing = sorted(set(secret_names) - {s.name for s in secrets})
    if missing:
        raise UnknownSecret(
            f"{app_name} is recorded as using {', '.join(missing)}, which the Secrets store does not hold"
        )
    db.query(AppSecret).filter(AppSecret.app_name == app_name).delete()
    for secret in secrets:
        db.add(AppSecret(app_name=app_name, secret_id=secret.id))
    db.commit()
