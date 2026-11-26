# Copyright Alejandro Martínez Corriá and the Thinkube contributors
# SPDX-License-Identifier: MIT

"""A CoNLL-U file becomes one row per sentence of its syntactic words."""

import duckdb
import pytest

from app.services import conllu, tables

# A multiword token (zum = zu dem), an empty node, comments, and "_" values
TWO_SENTENCES = (
    "# newdoc id = d1\n"
    "# sent_id = s1\n"
    "# text = Er geht zum Haus.\n"
    "1\tEr\ter\tPRON\tPPER\tCase=Nom\t2\tnsubj\t_\t_\n"
    "2\tgeht\tgehen\tVERB\tVVFIN\t_\t0\troot\t_\t_\n"
    "3-4\tzum\t_\t_\t_\t_\t_\t_\t_\t_\n"
    "3\tzu\tzu\tADP\tAPPR\t_\t5\tcase\t_\t_\n"
    "4\tdem\tder\tDET\tART\tCase=Dat\t5\tdet\t_\t_\n"
    "4.1\tdort\t_\t_\t_\t_\t_\t_\t_\t_\n"
    "5\tHaus\tHaus\tNOUN\tNN\t_\t2\tobl\t_\tSpaceAfter=No\n"
    "6\t.\t.\tPUNCT\t$.\t_\t2\tpunct\t_\t_\n"
    "\n"
    "# sent_id = s2\n"
    "1\tJa\tja\tINTJ\tITJ\t_\t0\troot\t_\t_\n"
)


@pytest.fixture
def treebank(tmp_path):
    path = tmp_path / "de_test-ud-train.conllu"
    path.write_text(TWO_SENTENCES, encoding="utf-8")
    return path


def test_a_sentence_keeps_its_syntactic_words_and_its_comments(treebank):
    first, second = list(conllu.sentences(treebank, "UD_German-Test"))
    assert first["tokens"] == ["Er", "geht", "zu", "dem", "Haus", "."]
    assert first["head"] == [2, 0, 5, 5, 2, 2]
    assert first["deprel"] == ["nsubj", "root", "case", "det", "obl", "punct"]
    assert first["feats"] == ["Case=Nom", "_", "_", "Case=Dat", "_", "_"]
    assert first["lemma"][2:4] == ["zu", "der"]
    assert (first["sent_id"], first["text"], first["treebank"]) == ("s1", "Er geht zum Haus.", "UD_German-Test")
    # The last sentence needs no blank line after it; a sentence without # text has none
    assert (second["tokens"], second["sent_id"], second["text"]) == (["Ja"], "s2", None)


def test_sentences_become_a_parquet_file_with_the_words_columns(treebank, tmp_path):
    target = tmp_path / "train.parquet"
    assert conllu.to_parquet([(treebank, "UD_German-Test"), (treebank, "UD_German-Other")], target) == 4
    with duckdb.connect() as db:
        relation = db.sql(f"SELECT * FROM read_parquet('{target}')")
        assert dict(zip(relation.columns, map(str, relation.types))) == conllu.COLUMNS
        assert [r[0] for r in db.sql(f"SELECT treebank FROM read_parquet('{target}')").fetchall()] == [
            "UD_German-Test", "UD_German-Test", "UD_German-Other", "UD_German-Other"]


def test_a_line_with_the_wrong_number_of_fields_is_refused_naming_it(tmp_path):
    path = tmp_path / "bad.conllu"
    path.write_text("1\tEr\ter\tPRON\n", encoding="utf-8")
    with pytest.raises(conllu.ConlluError, match="bad.conllu line 1 has 4 fields, not 10"):
        list(conllu.sentences(path, None))


def test_a_file_without_sentences_is_refused(tmp_path):
    path = tmp_path / "empty.conllu"
    path.write_text("# only a comment\n\n", encoding="utf-8")
    with pytest.raises(conllu.ConlluError, match="empty.conllu hold no sentence"):
        conllu.to_parquet([(path, None)], tmp_path / "out.parquet")


def test_an_uploaded_conllu_file_is_read_as_sentences(treebank, tmp_path):
    target = tmp_path / "upload.parquet"
    tables.to_parquet(treebank, "de_test-ud-train.conllu", target)
    rows = tables.head(target, 0, 10)
    assert [r["treebank"] for r in rows] == ["de_test-ud-train", "de_test-ud-train"]
