#!/usr/bin/env python3
"""Tests that a component registers as a component, not as a user app.

The ConfigMap label is set from `_is_component()` in deploy_application.py while
the generated body used to hardcode `user_app`, so every component deployment
produced a label and a body that disagreed. Components also get their own
category rather than sitting among the user applications.
"""

import pytest

from app.api.service_discovery_config import (
    APP_CATEGORY,
    APP_SERVICE_TYPE,
    COMPONENT_CATEGORY,
    COMPONENT_SERVICE_TYPE,
    Deployment,
)


def resolve(deployment: Deployment):
    """The type/category pair the generator emits for a deployment block."""
    if deployment.is_component:
        return COMPONENT_SERVICE_TYPE, COMPONENT_CATEGORY
    return APP_SERVICE_TYPE, APP_CATEGORY


def test_component_gets_component_type_and_its_own_category():
    service_type, category = resolve(Deployment(type="component"))

    assert service_type == "component"
    assert category == "components"


@pytest.mark.parametrize("declared_type", ["app", "knative"])
def test_everything_else_stays_a_user_app(declared_type):
    service_type, category = resolve(Deployment(type=declared_type))

    assert service_type == "user_app"
    assert category == "applications"


def test_component_category_is_not_the_application_category():
    """Components must be separable from user apps in the dashboard."""
    assert COMPONENT_CATEGORY != APP_CATEGORY


def test_component_is_an_accepted_service_type():
    """The services table constrains type; component has to be in that set."""
    from app.models.service_schemas import ServiceType

    assert COMPONENT_SERVICE_TYPE in ServiceType.__args__
    assert APP_SERVICE_TYPE in ServiceType.__args__


def test_components_can_still_be_disabled():
    """Changing the type must not silently remove the disable control."""
    from app.models.services import Service

    disableable = Service(name="x", type=COMPONENT_SERVICE_TYPE).can_be_disabled

    assert disableable is True
