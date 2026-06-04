from context_curator.eval.real_corpus import entities_match, extract_entities
from context_curator.replay.schema import ToolCall


def _c(name, **args):
    return ToolCall(call_id="x", name=name, args=args)


def test_extract_path_args():
    assert extract_entities(_c("Read", file_path="/a/b.py"))
    assert extract_entities(_c("Grep", path="/a"))
    assert extract_entities(_c("NotebookEdit", notebook_path="/a/n.ipynb"))
    # pattern-only Glob (no path) yields no entity
    assert extract_entities(_c("Glob", pattern="**/*.py")) == set()
    # path-less tool yields nothing
    assert extract_entities(_c("Bash", command="ls")) == set()


def test_equivalence_exact_and_dir_containment():
    read = extract_entities(_c("Read", file_path="/a/b.py"))
    grep_dir = extract_entities(_c("Grep", path="/a"))
    other = extract_entities(_c("Read", file_path="/c/d.py"))
    assert entities_match(read, read)            # exact
    assert entities_match(grep_dir, read)        # /a contains /a/b.py
    assert not entities_match(read, other)       # disjoint
    assert not entities_match(extract_entities(_c("Glob", pattern="*")), read)  # empty never match
