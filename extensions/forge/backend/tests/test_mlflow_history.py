import asyncio

from app.services import mlflow


def test_a_metric_history_is_read_page_after_page(monkeypatch):
    pages = {
        None: {"metrics": [{"step": s, "value": float(s), "timestamp": s} for s in range(3)], "next_page_token": "p2"},
        "p2": {"metrics": [{"step": s, "value": float(s), "timestamp": s} for s in range(3, 5)], "next_page_token": "p3"},
        "p3": {"metrics": [{"step": 5, "value": 5.0, "timestamp": 5}]},
    }
    asked = []

    async def call(client, token, method, path, **kwargs):
        assert (method, path) == ("GET", "metrics/get-history")
        assert kwargs["params"]["max_results"] == mlflow.HISTORY_PAGE
        asked.append(kwargs["params"].get("page_token"))
        return pages[kwargs["params"].get("page_token")]

    monkeypatch.setattr(mlflow, "_call", call)
    found = asyncio.run(mlflow._history(None, "token", "r1", "loss"))

    assert asked == [None, "p2", "p3"]
    assert [m["step"] for m in found] == [0, 1, 2, 3, 4, 5]


def test_a_step_logged_again_after_a_resume_keeps_its_last_value():
    values = [
        {"step": 10, "value": 0.5, "timestamp": 100},
        {"step": 11, "value": 0.6, "timestamp": 101},
        {"step": 12, "value": 0.7, "timestamp": 102},
        # the resumed session runs steps 11 and 12 again
        {"step": 11, "value": 0.61, "timestamp": 200},
        {"step": 12, "value": 0.71, "timestamp": 201},
        {"step": 13, "value": 0.8, "timestamp": 202},
    ]

    assert mlflow.by_step(values) == [
        {"step": 10, "value": 0.5}, {"step": 11, "value": 0.61},
        {"step": 12, "value": 0.71}, {"step": 13, "value": 0.8},
    ]


def test_the_history_is_in_the_order_of_the_steps():
    values = [{"step": 3, "value": 3.0, "timestamp": 1}, {"step": 1, "value": 1.0, "timestamp": 2}]

    assert [m["step"] for m in mlflow.by_step(values)] == [1, 3]
