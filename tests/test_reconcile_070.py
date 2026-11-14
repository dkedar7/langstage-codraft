"""0.7.0: the agent's tools and prompt agree with what the renderer does, the agent can
restore a deleted block, a Codraft-exported PDF reads back as whole words, uploads are found
from any working directory, and the "what the author changed" note survives a restart."""
import json
import os
import sqlite3
import time
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage

from langstage_codraft import textutil
from langstage_codraft.mcp_server import describe_tools
from langstage_codraft.middleware import CODRAFT_PROMPT, CodraftMiddleware
from langstage_codraft.store import BLOCK_VARIANTS, VARIANT_NOTES, Store, set_store
from langstage_codraft.tools import (
    CODRAFT_TOOLS,
    get_document,
    list_deleted_blocks,
    propose_edit,
    read_blocks,
    restore_block,
    set_typography,
)

DATA = Path(__file__).parent / "data"


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


# ------------------------------------------------------------ the Markdown dialect
def test_the_agent_is_told_the_markdown_the_renderer_renders():
    for text in (CODRAFT_PROMPT, propose_edit.description):
        flat = " ".join(text.split())
        assert "x^2^" in flat and "H~2~O" in flat and "<sup>2</sup>" in flat
        assert "~~text~~" in flat and "strikes" in flat
        # 0.6 said the opposite: that ^2^ and ~2~ print literally
        assert "~2~ and LaTeX" not in flat and "`^2^`, `~2~`" not in flat
        assert "LaTeX ($x^2$) prints literally" in flat


# ---------------------------------------------------------------------- variants
def test_the_outline_says_what_each_variant_looks_like(store, cfg_for):
    d = store.create_document("Doc")
    out = call(get_document, cfg_for(d["id"]))
    line = next(ln for ln in out.splitlines() if ln.startswith("What each variant looks like:"))
    for names in BLOCK_VARIANTS.values():
        for v in names:
            assert f"{v} = {VARIANT_NOTES[v]}" in line
    # the thing an agent got wrong: which heading variants follow the document's settings
    assert "section = " in line and "replaces that default" in line
    assert "it ignores heading_case" in line
    assert line.count("figure-box = ") == 1  # shared by four block types, described once


def test_every_variant_has_a_note():
    assert {v for names in BLOCK_VARIANTS.values() for v in names} <= set(VARIANT_NOTES)


# -------------------------------------------------------------------- hyphenation
def test_set_typography_sets_hyphenation(store, cfg_for):
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    out = call(set_typography, cfg, text_align="justify", hyphens="auto")
    assert '"hyphens": "auto"' in out
    assert store.get_document(d["id"])["settings"]["style"] == {"text_align": "justify", "hyphens": "auto"}
    call(set_typography, cfg, hyphens="manual")
    assert store.get_document(d["id"])["settings"]["style"]["hyphens"] == "manual"
    bad = call(set_typography, cfg, hyphens="sometimes")
    assert bad.startswith("ERROR") and "hyphens" in bad
    assert store.get_document(d["id"])["settings"]["style"]["hyphens"] == "manual"
    assert "hyphens" in set_typography.description


# ---------------------------------------------------------- restore a deleted block
def test_the_agent_lists_and_restores_deleted_blocks(store, cfg_for):
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    a, b, c = (store.insert_block(d["id"], "text", {"markdown": t}) for t in ("First.", "Second sources.", "Third."))
    store.edit_block(b["id"], content={"markdown": "Second sources, revised."})
    assert call(list_deleted_blocks, cfg) == "No deleted blocks in this document."
    store.delete_block(b["id"])
    listed = call(list_deleted_blocks, cfg)
    assert f"[{b['id']}] text" in listed and "Second sources, revised." in listed and "2 version(s)" in listed
    # a deleted block's error now says how to get it back
    assert "restore_block" in call(read_blocks, cfg, block_ids=[b["id"]])
    out = call(restore_block, cfg, block_id=b["id"])
    assert out.startswith(f"Restored text block {b['id']}") and "applied immediately" in out
    assert [x["id"] for x in store.list_blocks(d["id"])] == [a["id"], b["id"], c["id"]]  # where it was
    assert store.get_block(b["id"])["n_versions"] == 2                                     # with its history
    assert store.list_suggestions(d["id"]) == []    # not a suggestion: nothing waits for approval
    assert call(list_deleted_blocks, cfg) == "No deleted blocks in this document."


def test_restore_refuses_what_is_not_a_deleted_block_of_this_document(store, cfg_for):
    d = store.create_document("Doc")
    other = store.create_document("Other")
    cfg = cfg_for(d["id"])
    live = store.insert_block(d["id"], "text", {"markdown": "Still here."})
    theirs = store.insert_block(other["id"], "text", {"markdown": "Someone else's."})
    store.delete_block(theirs["id"])
    assert "not deleted" in call(restore_block, cfg, block_id=live["id"])
    out = call(restore_block, cfg, block_id=theirs["id"])   # another document's block (gh #25)
    assert out.startswith("ERROR") and theirs["id"] in out
    assert [b["id"] for b in store.deleted_blocks(other["id"])] == [theirs["id"]]
    assert call(restore_block, cfg, block_id="blk_nope").startswith("ERROR")
    assert call(restore_block, cfg, block_id="sug_nope").startswith("ERROR")


def test_restore_works_in_approval_mode_and_over_mcp(store, cfg_for):
    d = store.create_document("Doc", settings={"approval": "required"})
    b = store.insert_block(d["id"], "heading", {"text": "Sources", "level": 2})
    store.delete_block(b["id"])
    assert "Restored heading block" in call(restore_block, cfg_for(d["id"]), block_id=b["id"])
    names = [t.name for t in CODRAFT_TOOLS]
    assert "list_deleted_blocks" in names and "restore_block" in names
    assert "list_deleted_blocks" in describe_tools() and "restore_block" in describe_tools()
    assert "list_deleted_blocks" in CODRAFT_PROMPT and "restore_block" in CODRAFT_PROMPT


# -------------------------------------------------- reading a Codraft export back in
def test_a_codraft_exported_pdf_reads_back_as_whole_words():
    data = (DATA / "hyphenated-export.pdf").read_bytes()
    # what pypdf alone makes of it: a space at every possible break point
    import io

    from pypdf import PdfReader

    raw = PdfReader(io.BytesIO(data)).pages[0].extract_text()
    assert "or gan i sa tions" in raw and "ap pre ci ate" in raw
    text = textutil.extract_text("report.pdf", data)
    flat = " ".join(text.split())
    assert "Representative organisations increasingly appreciate that comprehensive infrastructure" in flat
    assert "interdepartmental communication appears consistently underdeveloped." in flat
    for broken in ("or gan", "ap pre", "infrastruc ture", "infrastruc- ture", "­", "￾", "\u0002"):
        assert broken not in flat
    assert flat.count("Representative organisations") == 6


def test_other_pdfs_are_still_read_with_pypdf(monkeypatch):
    import io

    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    w.add_metadata({"/Producer": "Acme Typesetter 3"})
    buf = io.BytesIO()
    w.write(buf)
    monkeypatch.setattr(textutil, "_pdfium_text", lambda data: (_ for _ in ()).throw(AssertionError("not for this PDF")))
    assert textutil.extract_text("a.pdf", buf.getvalue()) == ""


def test_a_browser_pdf_falls_back_to_pypdf_when_pdfium_fails(monkeypatch):
    data = (DATA / "hyphenated-export.pdf").read_bytes()
    monkeypatch.setattr(textutil, "_pdfium_text", lambda data: None)
    assert "Representative" in textutil.extract_text("report.pdf", data)


# ------------------------------------------------ uploads and the working directory
def test_uploads_are_found_from_another_working_directory(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    s = Store(Path(".codraft") / "codraft.db")       # how `run` opens it: relative to the cwd
    d = s.create_document("Doc")
    u = s.add_upload(d["id"], "logo.png", b"PNGDATA", purpose="asset", mime="image/png")
    assert Path(u["path"]).is_absolute() and Path(u["path"]).read_bytes() == b"PNGDATA"
    s.close()

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    s2 = Store(project / ".codraft" / "codraft.db")  # `export --data-dir` from another directory
    try:
        assert Path(s2.get_upload(u["id"])["path"]).read_bytes() == b"PNGDATA"
        assert [Path(x["path"]).exists() for x in s2.list_uploads(d["id"])] == [True]
    finally:
        s2.close()


def test_uploads_saved_by_an_older_version_are_still_found(tmp_path, monkeypatch):
    """Before 0.7.0 the row held a path relative to the server's working directory."""
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    s = Store(Path(".codraft") / "codraft.db")
    d = s.create_document("Doc")
    u = s.add_upload(d["id"], "notes.md", b"# Notes", text="# Notes")
    page = s.uploads_dir / f"{u['id']}_p1.jpg"
    page.write_bytes(b"JPG")
    name = Path(u["path"]).name
    old = os.path.join(".codraft", "uploads", name)
    s._db.execute("UPDATE uploads SET path = ?, meta = ? WHERE id = ?",
                  (old, json.dumps({"pages": [os.path.join(".codraft", "uploads", page.name)]}), u["id"]))
    s._db.commit()
    assert Path(s.get_upload(u["id"])["path"]).read_bytes() == b"# Notes"     # same cwd: as before
    s.close()

    monkeypatch.chdir(tmp_path)
    s2 = Store(project / ".codraft" / "codraft.db")
    try:
        got = s2.get_upload(u["id"])
        assert Path(got["path"]).read_bytes() == b"# Notes"
        assert [Path(p).read_bytes() for p in got["pages"]] == [b"JPG"]
        # a data directory that was moved: the absolute path saved with the row is stale
        s2._db.execute("UPDATE uploads SET path = ? WHERE id = ?", (str(tmp_path / "gone" / "uploads" / name), u["id"]))
        s2._db.commit()
        assert Path(s2.get_upload(u["id"])["path"]).read_bytes() == b"# Notes"
        s2.delete_upload(u["id"])                    # and it deletes the right files
        assert not (s2.uploads_dir / name).exists() and not page.exists()
    finally:
        s2.close()


def test_the_upload_file_route_serves_from_another_working_directory(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from langstage_codraft.agent import build_demo_agent
    from langstage_codraft.config import CodraftConfig
    from langstage_codraft.server import create_app

    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    s = Store(Path("data") / "codraft.db")
    d = s.create_document("Doc")
    u = s.add_upload(d["id"], "pic.png", b"PNG", purpose="asset", mime="image/png")
    s._db.execute("UPDATE uploads SET path = ? WHERE id = ?",
                  (os.path.join("data", "uploads", Path(u["path"]).name), u["id"]))
    s._db.commit()
    s.close()
    monkeypatch.chdir(tmp_path)
    cfg = CodraftConfig.resolve(overrides={"data_dir": project / "data", "port": 8798}, use_toml=False)
    store = Store(project / "data" / "codraft.db")
    app = create_app(cfg, build_demo_agent(), {"kind": "demo", "label": "demo", "missing_tools": []}, store=store)
    with TestClient(app) as c:
        assert c.get(f"/api/uploads/{u['id']}/file").content == b"PNG"


# ---------------------------------------------- the turn note survives a restart
class _Request:
    def __init__(self, messages, system=None):
        self.messages, self.system_message = messages, system
        self.overrides: dict = {}

    def override(self, **kw):
        new = _Request(kw.get("messages", self.messages), kw.get("system_message", self.system_message))
        new.overrides = kw
        return new


def _prepare(mw, doc_id, messages, monkeypatch):
    import langgraph.config

    monkeypatch.setattr(langgraph.config, "get_config", lambda: {"configurable": {"thread_id": doc_id}})
    return mw._prepare(_Request(messages))


def test_the_turn_note_survives_a_restart(tmp_path, monkeypatch):
    path = tmp_path / "codraft.db"
    s = Store(path)
    set_store(s)
    try:
        d = s.create_document("Doc")
        a = s.insert_block(d["id"], "text", {"markdown": "First."})
        mw = CodraftMiddleware(attach_images=False)
        turn1 = [HumanMessage(content="Draft something.", id="h1")]
        assert len(_prepare(mw, d["id"], turn1, monkeypatch).messages) == 1
        assert s.agent_turn(d["id"])["key"] == "h1"
        time.sleep(0.02)
        s.edit_block(a["id"], content={"markdown": "First, by the author."})
        s.close()

        s = Store(path)                               # the server restarts
        set_store(s)
        assert s._agent_turns == {} and s.agent_turn(d["id"])["key"] == "h1"
        turn2 = turn1 + [AIMessage(content="Done."), HumanMessage(content="Now the intro.", id="h2")]
        out = _prepare(CodraftMiddleware(attach_images=False), d["id"], turn2, monkeypatch)
        assert len(out.messages) == len(turn2) + 1
        note = out.messages[-2].content
        assert note.startswith("[Codraft note") and a["id"] in note and "edited by the author" in note
        # a second model call of the same turn, after another restart, repeats the same note
        s.close()
        s = Store(path)
        set_store(s)
        out2 = _prepare(CodraftMiddleware(attach_images=False), d["id"], turn2, monkeypatch)
        assert out2.messages[-2].content == note
    finally:
        set_store(None)
        s.close()


def test_a_database_from_an_older_version_gets_the_turn_table(tmp_path):
    path = tmp_path / "codraft.db"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("DROP TABLE agent_turns")
    db.commit()
    db.close()
    s = Store(path)
    try:
        d = s.create_document("Doc")
        assert s.agent_turn(d["id"]) is None
        s.set_agent_turn(d["id"], "h1", "", 1.0)
        assert s.agent_turn(d["id"]) == {"key": "h1", "note": "", "last_call": 1.0}
    finally:
        s.close()
