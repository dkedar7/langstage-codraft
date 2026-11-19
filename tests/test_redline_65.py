"""Redline Word export (gh #65): what changed since a checkpoint, or the pending
suggestions, written as Word's own tracked changes (``w:ins`` / ``w:del``), so a reviewer
who works in Word gets Accept / Reject.

The strongest check here is the round trip: accepting every tracked change in the
redline must read exactly like the document now (or with the suggestions accepted), and
rejecting every one exactly like the document then (or as it is without them).
"""
from __future__ import annotations

import io
import re
import zipfile

import docx
import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from lxml import etree

from langstage_codraft import cli, exporters, standalone
from langstage_codraft import server as server_mod
from langstage_codraft.agent import build_demo_agent
from langstage_codraft.config import CodraftConfig
from langstage_codraft.server import _references, create_app
from langstage_codraft.store import NotFound, Store

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W}


def q(tag: str) -> str:
    return f"{{{W}}}{tag}"


# ------------------------------------------------------------------ reading a redline
def body(data: bytes):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return etree.fromstring(z.read("word/document.xml")).find(q("body"))


def inside(el, tag: str) -> bool:
    p = el.getparent()
    while p is not None:
        if p.tag == q(tag):
            return True
        p = p.getparent()
    return False


def words(data: bytes, keep: str) -> list[str]:
    """The document's words with every change accepted (``keep="ins"``) or rejected
    (``keep="del"``)."""
    out = []
    for t in body(data).iter(q("t"), q("delText")):
        if keep == "ins" and (t.tag == q("delText") or inside(t, "del")):
            continue
        if keep == "del" and inside(t, "ins"):
            continue
        out.append(t.text or "")
    return "".join(f"{w} " for w in out).split()


def plain_words(data: bytes) -> list[str]:
    return words(data, "ins")


def tracked(data: bytes, op: str) -> list[str]:
    """The text of each ``w:ins`` / ``w:del`` run wrapper, in order."""
    tag = "delText" if op == "del" else "t"
    return ["".join(t.text or "" for t in el.iter(q(tag))) for el in body(data).iter(q(op))
            if el.getparent().tag != q("rPr") and el.getparent().tag != q("trPr")]


def check_well_formed(data: bytes) -> None:
    """What Word needs to open a document with revisions: unique revision ids, an author
    and date on each, no live text inside a deletion and no deleted text outside one."""
    b = body(data)
    marks = list(b.iter(q("ins"), q("del")))
    ids = [m.get(q("id")) for m in marks]
    assert len(ids) == len(set(ids)), "revision ids must be unique"
    for m in marks:
        assert m.get(q("author")) and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", m.get(q("date")))
    for t in b.iter(q("t")):
        assert not inside(t, "del"), "w:t inside a deletion"
    for t in b.iter(q("delText")):
        assert inside(t, "del"), "w:delText outside a deletion"
    # Revision runs wrap w:r only, and a paragraph mark's revision is the first child of its rPr.
    for m in marks:
        parent = m.getparent()
        if parent.tag == q("rPr"):
            assert parent.getparent().tag == q("pPr") and parent[0] is m
        elif parent.tag != q("trPr"):
            assert all(c.tag == q("r") for c in m)
    docx.Document(io.BytesIO(data))  # python-docx opens it


def export(store, did, plan=None, agent_name="Drafter"):
    return exporters.to_docx(store, did, {}, _references(store, did), plan, agent_name)


# ----------------------------------------------------------------------- since
def _report(store):
    d = store.create_document("Brief")
    did = d["id"]
    h = store.insert_block(did, "heading", {"text": "Contract brief", "level": 1}, author="Dana")
    p = [store.insert_block(did, "text", {"markdown": f"Clause {i} says the **supplier** pays within {i + 1}0 days."},
                            author="Dana") for i in range(5)]
    t = store.insert_block(did, "table", {"rows": [["Item", "Cost"], ["Hosting", "$10"]], "header": True},
                           author="Dana")
    c = store.insert_block(did, "callout", {"markdown": "Note: *draft* only."}, author="Dana")
    return did, h, p, t, c


def _change_since(store, did, h, p, t, c):
    store.edit_block(h["id"], content={"text": "Contract brief for legal", "level": 1}, author="Dana")
    store.edit_block(p[0]["id"], content={"markdown": "Clause 0 says the **buyer** pays within 10 days."},
                     author="user")
    s = store.propose(did, "edit", {"content": {"markdown": "Clause 1 says the **supplier** pays within 45 days.\n\n"
                                                            "- a new bullet"}}, block_id=p[1]["id"])
    store.accept_suggestion(s["id"])
    store.delete_block(p[2]["id"])
    store.move_block(p[4]["id"], None)  # to the start
    store.insert_block(did, "text", {"markdown": "A brand new closing paragraph."}, after=p[3]["id"], author="Lee")
    store.edit_block(t["id"], content={"rows": [["Item", "Cost"], ["Hosting", "$12"]], "header": True}, author="Dana")
    store.edit_block(c["id"], content={"markdown": "Note: *final* draft."}, author="Dana")


def test_a_redline_since_a_checkpoint_accepts_to_now_and_rejects_to_then(store):
    did, h, p, t, c = _report(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    then = plain_words(export(store, did))
    _change_since(store, did, h, p, t, c)
    now = plain_words(export(store, did))
    red = export(store, did, store.redline_since(did, cp["id"]))
    check_well_formed(red)
    assert words(red, "ins") == now
    assert words(red, "del") == then
    # And the same when the content really goes back: rejecting reads like the restore.
    store.restore_checkpoint(did, cp["id"])
    assert plain_words(export(store, did)) == then


def test_each_kind_of_change_is_marked_as_word_expects(store):
    did, h, p, t, c = _report(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    _change_since(store, did, h, p, t, c)
    red = export(store, did, store.redline_since(did, cp["id"]))
    ins, dels = tracked(red, "ins"), tracked(red, "del")
    # Edited text: only the changed words are marked, the rest of the sentence is plain.
    assert "supplier" in dels and "buyer" in ins
    assert not any("Clause 0 says" in x for x in ins + dels)
    assert "for legal" in "".join(ins)  # the heading, word by word
    assert "45 " in "".join(ins) and "20 " in "".join(dels)
    assert "a new bullet" in ins  # a paragraph added to an edited block
    # Deleted block: its text, deleted, with its paragraph mark.
    assert any("Clause 2 says" in x for x in dels)
    # Added block.
    assert "A brand new closing paragraph." in ins
    # Moved block: deleted where it was, inserted where it is.
    assert sum("Clause 4 says" in x for x in dels) == 1 and sum("Clause 4 says" in x for x in ins) == 1
    b = body(red)
    first = "".join(t.text or "" for t in b.find(q("p")).iter(q("t")))
    assert first.startswith("Clause 4 says")  # its new place is first
    # Table: the old table deleted (rows marked) and the new one inserted.
    assert "$10" in "".join(dels) and "$12" in "".join(ins)
    assert b.findall(".//w:trPr/w:del", NS) and b.findall(".//w:trPr/w:ins", NS)
    # Callout: word diff, still in the Quote style.
    assert "draft" in dels and "final" in ins
    assert "brief" not in "".join(dels)  # "brief" -> "brief for legal" adds words, deletes none


def test_formatting_survives_inside_tracked_runs(store):
    did, h, p, t, c = _report(store)
    cp = store.create_checkpoint(did, "v1")
    store.edit_block(p[0]["id"], content={"markdown": "Clause 0 says the **buyer** pays within 10 days."},
                     author="Dana")
    red = export(store, did, store.redline_since(did, cp["id"]))
    for op, text in (("ins", "buyer"), ("del", "supplier")):
        tag = "t" if op == "ins" else "delText"
        hit = [r for r in body(red).iter(q("r")) if inside(r, op)
               and "".join(x.text or "" for x in r.iter(q(tag))).strip() == text]
        assert hit and hit[0].find("w:rPr/w:b", NS) is not None, f"{text} keeps its bold"


def test_authors_and_dates_come_from_the_records(store):
    did, h, p, t, c = _report(store)
    cp = store.create_checkpoint(did, "v1")
    _change_since(store, did, h, p, t, c)
    red = export(store, did, store.redline_since(did, cp["id"]), agent_name="Drafter")
    b = body(red)

    def authors_of(text: str) -> set[str]:
        got = set()
        for m in b.iter(q("ins"), q("del")):
            if text in "".join(x.text or "" for x in m.iter(q("t"), q("delText"))):
                got.add(m.get(q("author")))
        return got

    assert authors_of("45") == {"Drafter"}         # accepted from the agent
    assert authors_of("buyer") == {"Author"}       # a human with no name ("user")
    assert authors_of("brand new") == {"Lee"}      # a named collaborator
    assert authors_of("for legal") == {"Dana"}


def test_no_changes_since_writes_no_revisions(store):
    did, *_ = _report(store)
    cp = store.create_checkpoint(did, "v1")
    red = export(store, did, store.redline_since(did, cp["id"]))
    assert not list(body(red).iter(q("ins"), q("del")))
    assert plain_words(red) == plain_words(export(store, did))


def test_a_plain_word_export_has_no_revisions(store):
    did, h, p, t, c = _report(store)
    store.create_checkpoint(did, "v1")
    _change_since(store, did, h, p, t, c)
    assert not list(body(export(store, did)).iter(q("ins"), q("del")))


def test_a_deleted_chart_is_named_since_its_old_picture_is_not_there(store):
    d = store.create_document("Charts")
    did = d["id"]
    ch = store.insert_block(did, "chart", {"spec": {"data": [], "layout": {}}, "caption": "Revenue by month"},
                            author="Dana")
    cp = store.create_checkpoint(did, "v1")
    store.delete_block(ch["id"])
    red = export(store, did, store.redline_since(did, cp["id"]))
    check_well_formed(red)
    assert "[chart]" in tracked(red, "del") and "Revenue by month" in tracked(red, "del")


def test_find_checkpoint_by_id_or_name(store):
    did, *_ = _report(store)
    cp = store.create_checkpoint(did, "Sent to legal")
    assert store.find_checkpoint(did, cp["id"])["id"] == cp["id"]
    assert store.find_checkpoint(did, "sent TO legal")["id"] == cp["id"]
    newer = store.create_checkpoint(did, "Sent to legal")
    assert store.find_checkpoint(did, "Sent to legal")["id"] == newer["id"]  # the newest of that name
    with pytest.raises(NotFound, match="no checkpoint 'Nope'"):
        store.find_checkpoint(did, "Nope")


# --------------------------------------------------------------------- pending
def _pending(store):
    did, h, p, t, c = _report(store)
    store.propose(did, "edit", {"content": {"markdown": "Clause 0 says the **buyer** pays within 10 days."}},
                  block_id=p[0]["id"], author="agent")
    first = store.propose(did, "insert", {"type": "text", "content": {"markdown": "Inserted after clause 1."},
                                          "after": p[1]["id"]}, author="agent")
    store.propose(did, "insert", {"type": "text", "content": {"markdown": "Then this, after the insert."},
                                  "after_suggestion": first["id"]}, author="agent")
    store.propose(did, "insert", {"type": "text", "content": {"markdown": "And one at the very end."}},
                  author="agent")
    store.propose(did, "delete", {}, block_id=p[2]["id"], author="agent")
    store.propose(did, "move", {"after": h["id"]}, block_id=p[4]["id"], author="agent")
    store.propose(did, "edit", {"content": {"rows": [["Item", "Cost"], ["Hosting", "$15"]], "header": True}},
                  block_id=t["id"], author="agent")
    return did


def test_pending_suggestions_accept_to_the_accepted_document_and_reject_to_now(store):
    did = _pending(store)
    now = plain_words(export(store, did))
    red = export(store, did, store.redline_pending(did), agent_name="Drafter")
    check_well_formed(red)
    assert words(red, "del") == now
    for s in store.list_suggestions(did):  # accept them all, in the order they were made
        store.accept_suggestion(s["id"])
    assert words(red, "ins") == plain_words(export(store, did))


def test_pending_suggestions_are_attributed_to_the_agent(store):
    did = _pending(store)
    red = export(store, did, store.redline_pending(did), agent_name="Drafter")
    assert {m.get(q("author")) for m in body(red).iter(q("ins"), q("del"))} == {"Drafter"}
    ins = tracked(red, "ins")
    assert "Inserted after clause 1." in ins and "Then this, after the insert." in ins
    assert ins.index("Inserted after clause 1.") < ins.index("Then this, after the insert.")


def test_no_pending_suggestions_is_a_clean_copy(store):
    did, *_ = _report(store)
    red = export(store, did, store.redline_pending(did))
    assert not list(body(red).iter(q("ins"), q("del")))


# ------------------------------------------------------------------- the route
class DocxRenderer:
    """Chromium's stand-in for a Word export: no pictures."""

    def __init__(self, real):
        self.real = real

    def call(self, value):
        return value

    async def acall(self, value):
        return value

    def structure(self, doc_id, scale=2.0, info=None):
        return {"page_px": (816, 1056), "pages": []}

    def close(self):
        if self.real is not None:
            self.real.close()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": tmp_path / "data", "port": 8799}, use_toml=False)
    st = Store(tmp_path / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=st)
    with TestClient(app) as c:
        fake = DocxRenderer(server_mod.get_renderer())
        monkeypatch.setattr(server_mod, "get_renderer", lambda: fake)
        c.store = st
        yield c


def test_the_export_route_writes_both_redlines(client):
    st = client.store
    did, h, p, t, c = _report(st)
    st.create_checkpoint(did, "Sent to legal")
    _change_since(st, did, h, p, t, c)
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx", "since": "Sent to legal"})
    assert r.status_code == 200, r.text
    assert 'filename="brief-redline.docx"' in r.headers["content-disposition"]
    check_well_formed(r.content)
    assert "buyer" in tracked(r.content, "ins")
    st.propose(did, "edit", {"content": {"markdown": "Suggested wording."}}, block_id=p[0]["id"])
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx", "pending": "true"})
    assert r.status_code == 200, r.text
    assert 'filename="brief-suggestions.docx"' in r.headers["content-disposition"]
    assert "Suggested wording." in "".join(tracked(r.content, "ins"))
    plain = client.get(f"/api/docs/{did}/export", params={"format": "docx"})
    assert 'filename="brief.docx"' in plain.headers["content-disposition"]
    assert not list(body(plain.content).iter(q("ins"), q("del")))


def test_the_export_route_refuses_what_it_cannot_do(client):
    did, *_ = _report(client.store)
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx", "since": "Nope"})
    assert r.status_code == 404 and "no checkpoint 'Nope'" in r.json()["detail"]
    r = client.get(f"/api/docs/{did}/export", params={"format": "pdf", "pending": "true"})
    assert r.status_code == 400 and "format=docx" in r.json()["detail"]
    client.store.create_checkpoint(did, "v1")
    r = client.get(f"/api/docs/{did}/export", params={"format": "docx", "since": "v1", "pending": "true"})
    assert r.status_code == 400 and "not both" in r.json()["detail"]


# --------------------------------------------------------------------- the CLI
class RecordingStandalone:
    calls: list = []

    def __init__(self, data_dir=None, *, cfg=None, store=None):
        pass

    def start(self):
        return self

    def close(self):
        pass

    def get(self, doc_id, fmt="pdf", **kw):
        RecordingStandalone.calls.append((doc_id, fmt, kw))
        name = f"{doc_id}-redline.docx" if kw.get("since") else f"{doc_id}.{fmt}"
        return httpx.Response(200, content=b"docx", headers={
            "content-type": "application/octet-stream", "content-disposition": f'attachment; filename="{name}"'})


def test_cli_passes_since_and_pending_through(monkeypatch, tmp_path):
    RecordingStandalone.calls = []
    monkeypatch.setattr(standalone, "Standalone", RecordingStandalone)
    out = tmp_path / "redline.docx"
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "-f", "docx", "--since", "Sent to legal",
                                        "-o", str(out), "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 0, res.output
    assert out.read_bytes() == b"docx"
    assert RecordingStandalone.calls[-1] == ("doc_a", "docx", {"since": "Sent to legal"})
    res = CliRunner().invoke(cli.main, ["export", "doc_a", "-f", "docx", "--pending",
                                        "-o", str(out), "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 0, res.output
    assert RecordingStandalone.calls[-1] == ("doc_a", "docx", {"pending": True})


@pytest.mark.parametrize("args", [["-f", "pdf", "--since", "v1"], ["--pending"], ["-f", "docx", "-f", "pdf", "--pending"],
                                  ["-f", "docx", "--since", "v1", "--pending"]])
def test_cli_refuses_a_redline_that_is_not_word(args, tmp_path):
    res = CliRunner().invoke(cli.main, ["export", "doc_a", *args, "--data-dir", str(tmp_path / "d")])
    assert res.exit_code == 2
    assert "--since" in res.output and "--pending" in res.output


def test_export_document_passes_the_redline_on(monkeypatch, tmp_path):
    seen = []

    class SA(RecordingStandalone):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def get(self, doc_id, fmt="pdf", **kw):
            seen.append(kw)
            return super().get(doc_id, fmt, **kw)

    monkeypatch.setattr(standalone, "Standalone", SA)
    assert standalone.export_document(tmp_path, "doc_a", "docx", since="v1") == b"docx"
    assert seen[-1] == {"strict": False, "since": "v1"}
    standalone.export_document(tmp_path, "doc_a", "docx", pending=True)
    assert seen[-1] == {"strict": False, "pending": True}
