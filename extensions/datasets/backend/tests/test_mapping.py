# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""Columns become training examples: chat turns, or text as it is."""

import pytest

from app.services.mapping import MappingError, apply, check, guess, output_format, preview


@pytest.mark.parametrize("columns,expected", [
    (["messages"], {"kind": "messages", "columns": {"messages": "messages"}}),
    (["id", "conversations"], {"kind": "sharegpt", "columns": {"conversations": "conversations"}}),
    (["instruction", "input", "output"], {"kind": "instruction", "columns": {"instruction": "instruction", "input": "input", "output": "output"}}),
    (["question", "answer"], {"kind": "prompt", "columns": {"prompt": "question", "completion": "answer"}}),
    (["Problem", "Solution"], {"kind": "prompt", "columns": {"prompt": "Problem", "completion": "Solution"}}),
    (["text"], {"kind": "text", "columns": {"text": "text"}}),
])
def test_common_column_names_are_guessed(columns, expected):
    assert guess(columns) == expected


def test_unknown_columns_guess_nothing():
    assert guess(["a", "b"]) is None


def test_a_prompt_with_a_system_column_becomes_three_turns():
    m = {"kind": "prompt", "columns": {"prompt": "q", "completion": "a", "system": "s"}}
    assert apply({"q": "Hi?", "a": "Hello", "s": "Be brief"}, m) == {"messages": [
        {"role": "system", "content": "Be brief"}, {"role": "user", "content": "Hi?"},
        {"role": "assistant", "content": "Hello"}]}


def test_an_instruction_input_joins_the_prompt():
    m = {"kind": "instruction", "columns": {"instruction": "i", "input": "x", "output": "o"}}
    assert apply({"i": "Translate", "x": "hola", "o": "hello"}, m)["messages"][0]["content"] == "Translate\n\nhola"


def test_sharegpt_roles_become_chat_roles():
    m = {"kind": "sharegpt", "columns": {"conversations": "c"}}
    row = {"c": [{"from": "human", "value": "Hi"}, {"from": "gpt", "value": "Hello"}]}
    assert [t["role"] for t in apply(row, m)["messages"]] == ["user", "assistant"]


def test_text_is_kept_as_it_is():
    m = {"kind": "text", "columns": {"text": "t"}}
    assert apply({"t": "Once upon a time"}, m) == {"text": "Once upon a time"}
    assert output_format(m) == "text"


def test_a_mapping_names_what_is_missing_or_absent():
    with pytest.raises(MappingError, match="completion"):
        check({"kind": "prompt", "columns": {"prompt": "q"}}, ["q", "a"])
    with pytest.raises(MappingError, match="no column"):
        check({"kind": "text", "columns": {"text": "body"}}, ["text"])
    with pytest.raises(MappingError, match="kind must be"):
        check({"kind": "dpo", "columns": {}}, [])


def test_a_row_that_does_not_fit_is_shown_with_its_reason():
    m = {"kind": "prompt", "columns": {"prompt": "q", "completion": "a"}}
    shown = preview([{"q": "Hi", "a": "Hello"}, {"q": "Hi", "a": None}], m)
    assert shown[0] == {"example": {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]}}
    assert "not text" in shown[1]["error"]
