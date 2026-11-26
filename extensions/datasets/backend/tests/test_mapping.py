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


def test_a_choice_row_keeps_its_candidates_target_and_right():
    m = check({"kind": "choice", "columns": {"prompt": "p", "candidates": "c", "target": "t", "right": "r"}},
              ["p", "c", "t", "r"])
    row = {"p": "Who paints?", "c": ["〔雪舟が〕", "〔なし〕"], "t": [0.5, 0.5], "r": [True, False]}
    assert apply(row, m) == {"prompt": "Who paints?", "candidates": ["〔雪舟が〕", "〔なし〕"], "target": [0.5, 0.5],
                             "right": [True, False]}
    assert output_format(m) == "choice"


def test_a_choice_row_that_does_not_fit_says_why():
    m = {"kind": "choice", "columns": {"prompt": "p", "candidates": "c", "target": "t", "right": "r"}}
    shown = preview([{"p": "q", "c": ["a"], "t": [1.0], "r": [True]},
                     {"p": "q", "c": ["a", "b"], "t": [0.5, 0.4], "r": [True, False]},
                     {"p": "q", "c": ["a", "b"], "t": [1.0], "r": [True, False]},
                     {"p": "q", "c": ["a", "b"], "t": [1.0, 0.0], "r": [1, 0]}], m)
    assert "at least 2 candidates" in shown[0]["error"]
    assert "sums to" in shown[1]["error"]
    assert "one probability" in shown[2]["error"]
    assert "true or false" in shown[3]["error"]


def test_a_choice_row_names_its_none_candidate_by_index():
    m = check({"kind": "choice", "columns": {"prompt": "p", "candidates": "c", "target": "t", "none": "n"}},
              ["p", "c", "t", "n"])
    row = {"p": "Who paints?", "c": ["〔雪舟が〕", "〔なし〕"], "t": [0.0, 1.0], "n": 1}
    assert apply(row, m)["none"] == 1
    shown = preview([{**row, "n": 2}, {**row, "n": True}], m)
    assert all("not the index of one of the 2 candidates" in s["error"] for s in shown)


# A sentence's words and a label of each, for Meep²'s task tagging
WORDS_ALL = {"tokens": "tokens", "upos": "upos", "xpos": "xpos", "feats": "feats", "lemma": "lemma",
             "head": "head", "deprel": "deprel"}
SENTENCE = {"_row": 7, "tokens": ["Das", "ist", "gut"], "upos": ["PRON", "AUX", "ADJ"], "xpos": ["PDS", "VAFIN", "ADJD"],
            "feats": ["Case=Nom", "_", "Degree=Pos"], "lemma": ["der", "sein", "gut"], "head": [3, 3, 0],
            "deprel": ["nsubj", "cop", "root"]}


def test_a_treebanks_columns_are_guessed_as_words():
    columns = ["tokens", "lemma", "upos", "xpos", "feats", "head", "deprel", "treebank", "sent_id", "text"]
    assert guess(columns) == {"kind": "words", "columns": WORDS_ALL}


def test_words_without_the_parser_columns_are_guessed_without_them():
    assert guess(["tokens", "upos", "head"]) == {"kind": "words", "columns": {"tokens": "tokens", "upos": "upos"}}


def test_a_words_row_keeps_only_the_mapped_parts():
    m = {"kind": "words", "columns": {"tokens": "tokens", "upos": "upos", "head": "head", "deprel": "deprel"}}
    assert apply(SENTENCE, m) == {"tokens": ["Das", "ist", "gut"], "upos": ["PRON", "AUX", "ADJ"],
                                  "head": [3, 3, 0], "deprel": ["nsubj", "cop", "root"]}
    assert output_format(m) == "words"


def test_every_part_of_a_words_row():
    assert apply(SENTENCE, {"kind": "words", "columns": WORDS_ALL}) == {k: v for k, v in SENTENCE.items() if k != "_row"}


def test_head_and_deprel_are_mapped_together():
    with pytest.raises(MappingError, match="head and deprel together"):
        check({"kind": "words", "columns": {"tokens": "tokens", "head": "head"}}, list(SENTENCE))
    with pytest.raises(MappingError, match="head and deprel together"):
        check({"kind": "words", "columns": {"tokens": "tokens", "deprel": "deprel"}}, list(SENTENCE))
    assert check({"kind": "words", "columns": WORDS_ALL}, list(SENTENCE)) == {"kind": "words", "columns": WORDS_ALL}


@pytest.mark.parametrize("change,message", [
    ({"upos": ["PRON", "AUX"]}, "row 7: column upos has 2 values for 3 words"),
    ({"head": [3, 4, 0]}, r"row 7: column head has the heads \[4\], outside 0..3"),
    ({"head": [3, "3", 0]}, "row 7: column head is not a list of word numbers"),
    ({"tokens": []}, "row 7: column tokens is not a list of words"),
    ({"deprel": ["nsubj", 1, "root"]}, "row 7: column deprel is not a list of text labels"),
])
def test_a_words_row_that_does_not_line_up_is_refused_naming_the_row(change, message):
    with pytest.raises(MappingError, match=message):
        apply({**SENTENCE, **change}, {"kind": "words", "columns": WORDS_ALL})
