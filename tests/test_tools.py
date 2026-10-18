import json

from langstage_codraft.tools import (
    CODRAFT_TOOLS,
    compile_references,
    get_block_history,
    get_document,
    list_comments,
    parse_content,
    propose_delete,
    propose_edit,
    propose_insert,
    read_blocks,
    read_upload,
    reply_to_comment,
    search_document,
    set_block_layout,
    set_document_style,
    view_pages,
)


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


def test_tool_names_unique():
    names = [t.name for t in CODRAFT_TOOLS]
    assert len(names) == len(set(names)) == 19


def test_config_param_hidden_from_model():
    for t in CODRAFT_TOOLS:
        assert "config" not in t.tool_call_schema.model_json_schema().get("properties", {}), t.name


def test_no_document_context(store):
    assert call(get_document, {"configurable": {}}).startswith("ERROR")
    assert "not found" in call(get_document, {"configurable": {"thread_id": "doc_nope"}})


def test_outline_and_read(store, cfg_for):
    d = store.create_document("Plan", {"brief": {"audience": "execs"}})
    h = store.insert_block(d["id"], "heading", {"text": "Intro", "level": 2})
    t = store.insert_block(d["id"], "text", {"markdown": "Some **bold** words here."}, layout={"height": 120},
                           refs=[{"title": "Src", "url": "https://s"}])
    store.add_comment(d["id"], "tighten", block_id=t["id"])
    cfg = cfg_for(d["id"])
    out = call(get_document, cfg)
    assert "audience=execs" in out and h["id"] in out and "1 open comment" in out
    assert "fixed height ~" in out and "1 ref(s)" in out
    body = call(read_blocks, cfg, block_ids=[t["id"], "blk_missing"])
    assert "Some **bold** words here." in body and "ref: Src https://s" in body and "not found" in body


def test_propose_edit_waits_for_approval(store, cfg_for):
    d = store.create_document("A")
    t = store.insert_block(d["id"], "text", {"markdown": "old"})
    out = call(propose_edit, cfg_for(d["id"]), block_id=t["id"], content="new text", note="clearer",
               references=[{"title": "R", "url": "u"}])
    assert "waiting for the author's approval" in out
    assert store.get_block(t["id"])["content"]["markdown"] == "old"
    s = store.list_suggestions(d["id"])[0]
    assert s["payload"]["content"] == {"markdown": "new text"} and s["payload"]["refs"][0]["title"] == "R"


def test_propose_insert_auto_applies(store, cfg_for):
    d = store.create_document("A", {"approval": "auto"})
    out = call(propose_insert, cfg_for(d["id"]), block_type="heading", content="## Results", heading_level=2,
               full_width=True)
    assert out.startswith("Applied")
    b = store.list_blocks(d["id"])[0]
    assert b["content"] == {"text": "Results", "level": 2} and b["layout"]["span"] == "full"
    assert call(propose_insert, cfg_for(d["id"]), block_type="hologram", content="x").startswith("ERROR")


def test_parse_content_tables_and_charts():
    t = parse_content("table", "| a | b |\n|---|---|\n| 1 | 2 |")
    assert t == {"rows": [["a", "b"], ["1", "2"]], "header": True}
    c = parse_content("chart", json.dumps({"mark": "bar"}))
    assert c == {"spec": {"mark": "bar"}}
    assert parse_content("image", "https://x/y.png") == {"src": "https://x/y.png"}


def test_comments_reply_resolve(store, cfg_for):
    d = store.create_document("A")
    t = store.insert_block(d["id"], "text", {"markdown": "x"})
    c = store.add_comment(d["id"], "make formal", block_id=t["id"])
    cfg = cfg_for(d["id"])
    listing = call(list_comments, cfg)
    assert c["id"] in listing and "make formal" in listing
    assert "resolved it" in call(reply_to_comment, cfg, comment_id=c["id"], message="done", resolve=True)
    assert call(list_comments, cfg) == "No open comments."
    hist = call(get_block_history, cfg, block_id=t["id"])
    assert "resolved comment: make formal" in hist and "agent: done" in hist


def test_styling_applies_directly(store, cfg_for):
    d = store.create_document("A")
    t = store.insert_block(d["id"], "image", {})
    cfg = cfg_for(d["id"])
    out = call(set_block_layout, cfg, block_id=t["id"], pin_page=1, pin_top_px=40, repeat="all")
    assert '"repeat": "all"' in out
    out = call(set_block_layout, cfg, block_id=t["id"], repeat="2, 4")
    assert store.get_block(t["id"])["layout"]["repeat"] == [2, 4]
    assert "3 col" in call(set_document_style, cfg, columns=3, theme="modern")
    assert call(set_document_style, cfg, theme="nope").startswith("ERROR")


def test_search_and_uploads(store, cfg_for):
    d = store.create_document("A")
    store.insert_block(d["id"], "text", {"markdown": "Photosynthesis converts light."})
    u = store.add_upload(d["id"], "notes.txt", b"", text="Chlorophyll absorbs red light strongly. " * 5)
    cfg = cfg_for(d["id"])
    out = call(search_document, cfg, query="light chlorophyll")
    assert u["id"] in out and "block [" in out
    assert "Chlorophyll" in call(read_upload, cfg, upload_id=u["id"], length=300)


def test_references_and_delete(store, cfg_for):
    d = store.create_document("A")
    a = store.insert_block(d["id"], "text", {"markdown": "a"}, refs=[{"title": "One", "url": "u1"}])
    store.insert_block(d["id"], "text", {"markdown": "b"}, refs=[{"title": "One again", "url": "u1"}, {"title": "Two"}])
    refs = call(compile_references, cfg_for(d["id"]))
    assert refs.count("\n") == 1 and "[2] Two" in refs
    assert "waiting" in call(propose_delete, cfg_for(d["id"]), block_id=a["id"])


def test_view_pages_without_renderer(store, cfg_for):
    d = store.create_document("A")
    msg = view_pages.invoke({"type": "tool_call", "id": "1", "name": "view_pages", "args": {}},
                            config=cfg_for(d["id"]))
    assert "unavailable" in msg.content and msg.artifact is None


def test_insert_blocks_in_order_with_layouts(store, cfg_for):
    from langstage_codraft.tools import insert_blocks

    d = store.create_document("Batch", {"approval": "auto", "columns": 3})
    out = call(insert_blocks, cfg_for(d["id"]), blocks=[
        {"type": "heading", "content": "An overview", "level": 1, "layout": {"variant": "display", "span": "full"}},
        {"type": "text", "content": "Opening paragraph.", "layout": {"dropcap": True}},
        {"type": "text", "content": "{page}", "layout": {"variant": "footer", "pin": {"page": 1, "top": 900}, "repeat": "odd"}},
        {"type": "video", "content": "x", "layout": {"variant": "display"}},
    ])
    blocks = store.list_blocks(d["id"])
    assert [b["type"] for b in blocks] == ["heading", "text", "text"]
    assert blocks[0]["layout"]["variant"] == "display" and blocks[1]["layout"]["dropcap"] is True
    assert blocks[2]["layout"]["repeat"] == "odd"
    assert "3: ERROR" in out  # display is not a video variant


def test_draw_figure_sanitizes_and_frames(store, cfg_for):
    from langstage_codraft.tools import draw_figure

    d = store.create_document("Fig", {"approval": "auto"})
    svg = '<svg viewBox="0 0 10 10" onload="alert(1)"><script>x</script><rect width="10" height="10"/></svg>'
    out = call(draw_figure, cfg_for(d["id"]), svg=svg, label="FIGURE 1", title="Layers", source="Source: us")
    assert out.startswith("Applied")
    b = store.list_blocks(d["id"])[0]
    assert b["layout"]["variant"] == "figure-box" and b["content"]["label"] == "FIGURE 1"
    data = open(store.get_upload(b["content"]["src"])["path"], encoding="utf-8").read()
    assert "script" not in data and "onload" not in data and "<rect" in data


def test_page_anchored_pin_and_custom_page(store, cfg_for):
    d = store.create_document("Mag", {"page_size": "custom", "page_w_mm": 203.2, "page_h_mm": 273.05})
    b = store.insert_block(d["id"], "image", {})
    out = call(set_block_layout, cfg_for(d["id"]), block_id=b["id"], pin_page=1, page_x_px=0, page_y_px=0,
               page_w_px=768, page_h_px=420)
    assert '"anchor": "page"' in out
    outline = call(get_document, cfg_for(d["id"]))
    assert "page 768x1032" in outline and "Variants:" in outline
