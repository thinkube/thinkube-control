# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

import pytest

from thinkube_mill import TaskError, task, track, show


def test_declaration_is_described():
    @task(inputs={"rows": "jsonl"}, outputs={"cleaned": "jsonl"}, settings={"min_words": 5})
    def clean(rows, settings):
        """Keep the rows with enough words."""
        yield from rows

    assert clean.describe() == {
        "name": "clean",
        "description": "Keep the rows with enough words.",
        "inputs": {"rows": "jsonl"},
        "outputs": {"cleaned": "jsonl"},
        "settings": {"min_words": 5},
    }


def test_a_task_is_still_a_function():
    @task(inputs={"rows": "jsonl"}, outputs={"doubled": "jsonl"})
    def double(rows):
        for row in track(rows, "doubling"):
            show(input=row, output=row * 2)
            yield row * 2

    assert list(double([1, 2, 3])) == [2, 4, 6]


def test_unknown_type_is_refused():
    with pytest.raises(TaskError, match="has type 'csv'"):

        @task(inputs={"rows": "csv"})
        def read(rows):
            pass


def test_parameter_that_is_not_an_input_is_refused():
    with pytest.raises(TaskError, match="parameter\\(s\\) table"):

        @task(inputs={"rows": "jsonl"})
        def read(rows, table):
            pass


def test_declared_input_without_parameter_is_refused():
    with pytest.raises(TaskError, match="input\\(s\\) gold"):

        @task(inputs={"rows": "jsonl", "gold": "jsonl"})
        def score(rows):
            pass


def test_yielding_needs_exactly_one_jsonl_output():
    with pytest.raises(TaskError, match="declares 0"):

        @task(inputs={"rows": "jsonl"}, outputs={"report": "json"})
        def summarise(rows):
            yield from rows


def test_a_name_cannot_be_input_and_output():
    with pytest.raises(TaskError, match="both an input and an output"):

        @task(inputs={"rows": "jsonl"}, outputs={"rows": "jsonl"})
        def same(rows):
            yield from rows
