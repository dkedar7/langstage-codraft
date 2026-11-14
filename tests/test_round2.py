"""What the re-evaluation of 0.8.0 (2026-11) still found on the agent's side.

Diagrams the agent couldn't see, sources deleted to hit a page count, guessed URLs when
there was no search, an image tool offered on a key that couldn't pay for it, length
targets reported as met without counting, and tool results from old turns re-sent on
every call.
"""
import sys
import time
import types

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from langstage_codraft import agent, render, tools
from langstage_codraft.middleware import (
    CODRAFT_PROMPT,
    CodraftMiddleware,
    _start_turn,
    _trim_old_turns,
)
from langstage_codraft.tools import (
    begin_turn,
    draw_figure,
    get_block_history,
    get_document,
    insert_blocks,
    propose_delete,
    propose_edit,
    propose_insert,
    read_blocks,
    sanitize_svg,
    split_paragraphs,
    view_pages,
)


def call(tool, cfg, **args):
    return tool.invoke(args, config=cfg)


SVG = ('<svg viewBox="0 0 400 200"><rect x="10" y="10" width="120" height="60" fill="#eee"/>'
       '<text x="20" y="45" font-size="14">Box</text></svg>')
LONG = ("The first paragraph sets out the claim in plain terms and says why it matters to the reader. " * 4
        + "\n\n" + "The second paragraph gives the evidence, one source at a time, with its limits stated. " * 4
        + "\n\n" + "The third paragraph draws the implication and says what the reader should do next. " * 4)


# ------------------------------------------------------------------ 1. diagrams
def test_arrowheads_sized_in_stroke_widths_are_brought_back_in_proportion():
    """Doc D: stroke-width 10 with markerWidth 11 and no markerUnits is a 110-unit arrowhead."""
    svg = ("<svg viewBox='0 0 1000 520'><defs><marker id='a' markerWidth='11' markerHeight='11' refX='8' "
           "refY='5.5' orient='auto'><path d='M0,0 L11,5.5 L0,11 z'/></marker></defs>"
           "<path d='M0 0 L100 0' stroke='#000' stroke-width='10' fill='none' marker-end='url(#a)'/></svg>")
    out = sanitize_svg(svg)
    assert 'markerUnits="userSpaceOnUse"' in out and 'markerWidth="30"' in out and 'markerHeight="30"' in out
    assert 'viewBox="0 0 11 11"' in out and "refX='8'" in out  # the drawing scales, the tip stays put
    assert sanitize_svg(out) == out  # already in user units: left alone


def test_arrowheads_in_proportion_are_left_alone():
    svg = ('<svg viewBox="0 0 100 100"><defs><marker id="a" markerWidth="6" markerHeight="6" refX="5" refY="3" '
           'orient="auto"><path d="M0,0 L6,3 L0,6 z"/></marker></defs>'
           '<line x1="0" y1="0" x2="50" y2="50" stroke="#000" stroke-width="2" marker-end="url(#a)"/></svg>')
    assert 'markerWidth="6"' in sanitize_svg(svg) and "userSpaceOnUse" not in sanitize_svg(svg)
    unused = '<svg viewBox="0 0 9 9"><defs><marker id="z" markerWidth="40" markerHeight="40"></marker></defs></svg>'
    assert 'markerWidth="40"' in sanitize_svg(unused)


class FakeFigures:
    """A renderer stand-in: records what draw_figure asked to see and answers with issues."""

    def __init__(self, issues=()):
        self.issues, self.asked = list(issues), []

    def call(self, value):
        return value

    def figure_shot(self, doc_id, ref, preview=False, svg=None):
        self.asked.append({"ref": ref, "preview": preview, "svg": svg})
        return {"path": f"{ref}.jpg", "page": 2, "width": 332, "height": 166, "issues": list(self.issues)}


def test_draw_figure_comes_back_with_a_preview_and_the_label_check(store, cfg_for, monkeypatch):
    d = store.create_document("Doc")
    fake = FakeFigures(['A line or arrow runs through "Less heat": move the label off the line.'])
    monkeypatch.setattr(render, "ensure_renderer", lambda: fake)
    text, art = draw_figure.func(svg=SVG, title="Flows", config=cfg_for(d["id"]))
    (sg,) = store.list_suggestions(d["id"], status="pending")
    assert fake.asked == [{"ref": sg["id"], "preview": True, "svg": sanitize_svg(SVG)}]
    assert art == {"images": [f"{sg['id']}.jpg"]}
    assert f"The figure's id is {sg['id']}" in text and "PREVIEW attached" in text and "page 2" in text
    assert "LABEL CHECK found 1 problem(s)" in text and 'runs through "Less heat"' in text and "ONE redraw" in text
    fake.issues = []
    text, _ = draw_figure.func(svg=SVG, block_id=sg["id"], config=cfg_for(d["id"]))
    assert "Redrew your pending figure" in text and "LABEL CHECK: no cut-off" in text
    assert "each arrow points the right way" in text
    # the string result (what an agent's tool message holds) carries the check too
    assert "LABEL CHECK" in call(draw_figure, cfg_for(d["id"]), svg=SVG, block_id=sg["id"])


def test_draw_figure_previews_the_block_itself_once_it_is_applied(store, cfg_for, monkeypatch):
    d = store.create_document("Doc", settings={"approval": "auto"})
    fake = FakeFigures()
    monkeypatch.setattr(render, "ensure_renderer", lambda: fake)
    text, _ = draw_figure.func(svg=SVG, config=cfg_for(d["id"]))
    (b,) = store.list_blocks(d["id"])
    assert fake.asked[-1]["ref"] == b["id"] and fake.asked[-1]["preview"] is False
    assert f"The figure's id is {b['id']}" in text


def test_draw_figure_without_a_renderer_still_draws(store, cfg_for, monkeypatch):
    d = store.create_document("Doc")
    monkeypatch.setattr(render, "ensure_renderer", lambda: None)
    text, art = draw_figure.func(svg=SVG, config=cfg_for(d["id"]))
    assert art is None and "no preview" in text and "view_pages" in text
    assert len(store.list_suggestions(d["id"], status="pending")) == 1

    class Broken(FakeFigures):
        def figure_shot(self, *a, **k):
            raise RuntimeError("Chromium fell over")

    monkeypatch.setattr(render, "ensure_renderer", lambda: Broken())
    text, art = draw_figure.func(svg=SVG, config=cfg_for(d["id"]))
    assert art is None and "No preview: RuntimeError" in text and "ERROR" not in text


def test_redraws_of_one_figure_are_capped_per_turn(store, cfg_for, monkeypatch):
    d = store.create_document("Doc", settings={"approval": "auto"})
    monkeypatch.setattr(render, "ensure_renderer", lambda: FakeFigures(["x"]))
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "turn-1")
    draw_figure.func(svg=SVG, config=cfg)
    (b,) = store.list_blocks(d["id"])
    texts = [draw_figure.func(svg=SVG.replace("Box", f"Box {i}"), block_id=b["id"], config=cfg)[0] for i in range(5)]
    assert "version" not in texts[0] and "This is version 3 of this figure" in texts[1]
    assert "simplify it" in texts[1] and "This is version 5" in texts[3]
    assert texts[4].startswith("ERROR") and "drawn 5 times this turn" in texts[4] and "tell the author" in texts[4]
    assert store.get_block(b["id"])["n_versions"] == 5  # the sixth was not recorded
    begin_turn(d["id"], "turn-2")  # the author spoke again: a new allowance
    assert "ERROR" not in draw_figure.func(svg=SVG.replace("Box", "Again"), block_id=b["id"], config=cfg)[0]


def test_the_prompt_and_the_tool_teach_simple_diagrams():
    for needle in ("LABEL CHECK", "never on\n  it", "ONE redraw", "five is the limit"):
        assert needle in CODRAFT_PROMPT, needle
    for needle in ("PREVIEW", "LABEL CHECK", "never on it", "At most 5 versions"):
        assert needle in draw_figure.description, needle


# ------------------------------------------------------------------ 2. sources are not cut
@pytest.mark.parametrize("approval", ["auto", "required"])
def test_deleting_the_reference_list_always_waits_for_the_author(store, cfg_for, approval):
    """Doc D, auto mode: the agent deleted the references block to reach three pages."""
    d = store.create_document("Doc", settings={"approval": approval})
    refs = store.insert_block(d["id"], "references", {"title": "References"})
    cited = store.insert_block(d["id"], "text", {"markdown": "A claim."},
                               refs=[{"title": "Source", "url": "https://example.org", "note": ""}])
    plain = store.insert_block(d["id"], "page-break", {})
    cfg = cfg_for(d["id"])
    out = call(propose_delete, cfg, block_id=refs["id"], note="keep it to three pages")
    assert "WAITING for the author" in out and "the reference list" in out and "page count" in out
    out = call(propose_delete, cfg, block_id=cited["id"])
    assert "WAITING for the author" in out and "cites 1 source(s)" in out
    ids = [b["id"] for b in store.list_blocks(d["id"])]
    assert refs["id"] in ids and cited["id"] in ids
    assert len(store.list_suggestions(d["id"], status="pending")) >= 2
    out = call(propose_delete, cfg, block_id=plain["id"])
    gone = plain["id"] not in [b["id"] for b in store.list_blocks(d["id"])]
    assert gone is (approval == "auto") and "WAITING for the author" not in out
    # the author can still say yes
    sg = next(s for s in store.list_suggestions(d["id"], status="pending") if s["block_id"] == refs["id"])
    store.accept_suggestion(sg["id"])
    assert refs["id"] not in [b["id"] for b in store.list_blocks(d["id"])]


def test_the_rule_against_cutting_covers_the_agents_own_content_and_every_mode():
    assert "not the author's and not your own, in any approval mode" in CODRAFT_PROMPT
    assert "the reference list and every\n  block that cites a source stay" in CODRAFT_PROMPT
    assert "always\nwaits for the author's approval" in propose_delete.description


# ------------------------------------------------------------------ 3. search and fetching
def _fake_ddgs(monkeypatch, text):
    class DDGSException(Exception):
        pass

    class RatelimitException(DDGSException):
        pass

    class DDGS:
        def text(self, query, max_results=5):
            return text(query, DDGSException, RatelimitException)

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    return {t.name: t for t in agent.research_tools()}


def test_no_results_is_not_reported_as_an_outage(monkeypatch):
    """The re-evaluation: ddgs raises "No results found." and the tool said rate-limited."""
    calls = []

    def text(query, DDGSException, RatelimitException):
        calls.append(query)
        if query == "limited":
            raise RatelimitException("202 Ratelimit")
        raise DDGSException("No results found.")

    search = _fake_ddgs(monkeypatch, text)["web_search"]
    out = search.invoke({"query": "nothing here"})
    assert calls == ["nothing here"]  # an answer: no retry
    assert out.startswith("No results for 'nothing here'") and "the search worked" in out and "ERROR" not in out
    out = search.invoke({"query": "limited"})
    assert calls.count("limited") == 2 and "rate-limiting" in out and 'not "no results"' in out


def test_guessed_urls_stop_being_opened_after_a_run_of_failures(monkeypatch, store, cfg_for):
    """Doc A's drafting turn: 27 fetches, about half of them guesses that failed."""
    def text(query, *_):
        return [{"title": "Real", "href": "https://example.org/real", "body": "B"}]

    t = _fake_ddgs(monkeypatch, text)
    opened = []

    def get(url, **kw):
        opened.append(url)
        req = httpx.Request("GET", url)
        if "real" in url:
            return httpx.Response(200, text="<html><body><p>Real page.</p></body></html>",
                                  headers={"content-type": "text/html"}, request=req)
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    monkeypatch.setattr(httpx, "get", get)
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "t1")
    outs = [t["fetch_url"].invoke({"url": f"https://example.org/guess-{i}"}, config=cfg) for i in range(8)]
    assert "there is no such page (404)" in outs[0] and "1 failed fetch(es) this turn" in outs[0]
    assert "Traceback" not in outs[0] and "developer.mozilla.org" not in outs[0]
    assert len(opened) == agent.MAX_FAILED_FETCHES and outs[6].startswith("ERROR: not fetched")
    assert "web_search" in outs[6] and "could not source" in outs[6]
    t["web_search"].invoke({"query": "real things"}, config=cfg)
    assert "Real page." in t["fetch_url"].invoke({"url": "https://example.org/real"}, config=cfg)
    # a URL the author wrote in their message is opened however many guesses failed
    _start_turn(cfg, [HumanMessage("Use https://example.org/real-report, please.", id="h9")])
    for i in range(7):
        t["fetch_url"].invoke({"url": f"https://example.org/guess-b{i}"}, config=cfg)
    assert "Real page." in t["fetch_url"].invoke({"url": "https://example.org/real-report"}, config=cfg)
    begin_turn(d["id"], "t2")  # a new turn starts with a clean count
    assert "404" in t["fetch_url"].invoke({"url": "https://example.org/guess-x"}, config=cfg)


def test_without_search_the_agent_is_told_not_to_guess_urls(monkeypatch):
    seen = {}

    class Recorder(CodraftMiddleware):
        def __init__(self, **kw):
            seen.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(agent, "CodraftMiddleware", Recorder)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    monkeypatch.setattr(agent, "search_available", lambda: False)
    agent.build_default_agent("openai:gpt-4.1")
    assert "NO web search tool" in seen["extra_prompt"] and "Do not guess URLs" in seen["extra_prompt"]
    monkeypatch.setattr(agent, "search_available", lambda: True)
    agent.build_default_agent("openai:gpt-4.1")
    assert seen["extra_prompt"] == ""


def test_check_says_whether_search_and_images_are_available(monkeypatch, tmp_path):
    from click.testing import CliRunner

    from langstage_codraft.cli import main

    monkeypatch.setenv("OPENAI_API_KEY", "sk-dummy")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent, "search_available", lambda: False)
    out = CliRunner().invoke(main, ["check", "--model", "openai:gpt-4.1", "--data-dir", str(tmp_path / "d")]).output
    assert "no web search" in out and "langstage-codraft[search]" in out and "generate_image is off" in out
    monkeypatch.setattr(agent, "search_available", lambda: True)
    out = CliRunner().invoke(main, ["check", "--model", "openai:gpt-4.1", "--data-dir", str(tmp_path / "d")]).output
    assert "web_search available" in out


def test_search_is_part_of_the_default_install():
    import tomllib
    from pathlib import Path

    deps = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert any(d.startswith("ddgs") for d in deps["dependencies"])
    assert "search" in deps["optional-dependencies"]  # the old extra still installs


# ------------------------------------------------------------------ 4. generate_image
def test_generate_image_is_only_offered_where_it_can_work(monkeypatch):
    """Doc D: an OpenAI model with an OpenRouter key in the environment got a tool that 402'd."""
    names = lambda spec: {t.name for t in agent.research_tools(spec)}  # noqa: E731
    monkeypatch.delenv("LANGSTAGE_CODRAFT_IMAGE_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    assert "generate_image" not in names("openai:gpt-5.4") and "generate_image" not in names(None)
    assert "generate_image" in names("openrouter:anthropic/claude-haiku-5.5")
    monkeypatch.setenv("LANGSTAGE_CODRAFT_IMAGE_MODEL", "google/gemini-3.1-flash-image")
    assert "generate_image" in names("openai:gpt-5.4")  # the deployer asked for it
    monkeypatch.delenv("OPENROUTER_API_KEY")
    assert "generate_image" not in names("openrouter:anthropic/claude-haiku-5.5")


@pytest.mark.parametrize("status, why", [(402, "no credit left"), (401, "rejected the API key")])
def test_a_key_or_credit_refusal_is_one_plain_line(monkeypatch, store, cfg_for, status, why):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-dummy")
    monkeypatch.setattr(httpx, "post", lambda url, **kw: httpx.Response(status, request=httpx.Request("POST", url)))
    d = store.create_document("Doc")
    out = agent.generate_image_tool().invoke({"prompt": "a roof"}, config=cfg_for(d["id"]))
    assert out.startswith("ERROR: image generation is unavailable") and why in out and f"({status})" in out
    assert "\n" not in out and "developer.mozilla.org" not in out and "draw_figure" in out


# ------------------------------------------------------------------ 5. word counts
def test_word_counts_are_where_a_length_target_needs_them(store, cfg_for):
    """The handbook: "tighten by about a third" came back 10% shorter and was called done."""
    d = store.create_document("Doc")
    b = store.insert_block(d["id"], "text", {"markdown": "one two three four five six seven eight nine ten"})
    cfg = cfg_for(d["id"])
    assert "Words: 10 in all (as accepted)" in call(get_document, cfg)
    assert f"[{b['id']}] text" in call(read_blocks, cfg, block_ids=[b["id"]])
    assert " · 10 words" in call(read_blocks, cfg, block_ids=[b["id"]])
    out = call(propose_edit, cfg, block_id=b["id"], content="one two three four five six seven")
    assert "Words: 10 -> 7 (-30%)" in out
    out = call(read_blocks, cfg, block_ids=[b["id"]])
    assert "has now, 10 words)" in out and "replaces it; 7 words)" in out
    assert "Words: 7 in all (with your pending suggestions applied)" in call(get_document, cfg)
    assert "check each edit's count against\n  it" in CODRAFT_PROMPT


# ------------------------------------------------------------------ 7. paragraphs and history
def test_a_long_draft_is_split_into_one_block_per_paragraph(store, cfg_for):
    """Doc B drafted whole sections into the template's one-paragraph placeholders."""
    d = store.create_document("Doc")
    stub = store.insert_block(d["id"], "text", {"markdown": "Write the introduction here."})
    after = store.insert_block(d["id"], "heading", {"text": "Next", "level": 2})
    cfg = cfg_for(d["id"])
    out = call(propose_edit, cfg, block_id=stub["id"], content=LONG,
               references=[{"title": "S", "url": "https://example.org", "note": ""}])
    assert "split into one block per paragraph" in out and "keep_together=True" in out
    store.accept_all(d["id"])
    blocks = store.list_blocks(d["id"])
    assert [b["type"] for b in blocks] == ["text", "text", "text", "heading"] and blocks[3]["id"] == after["id"]
    assert [b["content"]["markdown"].split()[1] for b in blocks[:3]] == ["first", "second", "third"]
    assert blocks[0]["id"] == stub["id"] and len(blocks[0]["refs"]) == 1 and not blocks[1]["refs"]


def test_a_draft_stays_whole_when_asked_or_when_it_is_the_authors_structure(store, cfg_for):
    d = store.create_document("Doc")
    stub = store.insert_block(d["id"], "text", {"markdown": "Stub."})
    theirs = store.insert_block(d["id"], "text", {"markdown": "Para one.\n\nPara two."})
    boxed = store.insert_block(d["id"], "text", {"markdown": "Stub."}, layout={"height": 300})
    cfg = cfg_for(d["id"])
    for bid, extra in ((stub["id"], {"keep_together": True}), (theirs["id"], {}), (boxed["id"], {})):
        assert "split" not in call(propose_edit, cfg, block_id=bid, content=LONG, **extra)
    assert "split" not in call(propose_edit, cfg, block_id=stub["id"], content="Short.\n\nAlso short.")
    assert all(s["kind"] == "edit" for s in store.list_suggestions(d["id"], status="pending"))


def test_inserts_split_long_text_too(store, cfg_for):
    d = store.create_document("Doc")
    cfg = cfg_for(d["id"])
    out = call(insert_blocks, cfg, blocks=[{"type": "heading", "content": "Intro"},
                                           {"type": "text", "content": LONG},
                                           {"type": "text", "content": LONG, "keep_together": True}])
    assert "1 long text item(s) were split" in out
    out = call(propose_insert, cfg, block_type="text", content=LONG.replace("paragraph", "part"))
    assert "long text item(s) were split" in out
    store.accept_all(d["id"])
    assert [b["type"] for b in store.list_blocks(d["id"])] == ["heading"] + ["text"] * 7


def test_split_paragraphs_keeps_lists_tables_and_code_with_their_paragraph():
    md = "Intro.\n\n- a\n- b\n\nSecond.\n\n```\ncode\n\nmore\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\nThird."
    assert split_paragraphs(md) == ["Intro.\n\n- a\n- b", "Second.\n\n```\ncode\n\nmore\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |",
                                    "Third."]


def test_history_is_flagged_where_it_exists_and_not_fetched_where_it_doesnt(store, cfg_for):
    """Doc A: four get_block_history calls on blocks that only had an open comment."""
    d = store.create_document("Doc")
    plain = store.insert_block(d["id"], "text", {"markdown": "Untouched."})
    revised = store.insert_block(d["id"], "text", {"markdown": "First wording."})
    store.edit_block(revised["id"], content={"markdown": "The author's second wording."})
    store.add_comment(d["id"], "Tighten this.", block_id=plain["id"])
    cfg = cfg_for(d["id"])
    out = call(read_blocks, cfg, block_ids=[plain["id"], revised["id"]])
    head_plain, head_revised = (next(line for line in out.splitlines() if line.startswith(f"[{b['id']}]"))
                                for b in (plain, revised))
    assert "HISTORY" not in head_plain
    assert "HISTORY: the author revised it 1 time(s); get_block_history before rewriting it" in head_revised
    assert "has no history to respect" in call(get_block_history, cfg, block_id=plain["id"])
    assert "version(s)" in call(get_block_history, cfg, block_id=revised["id"])
    assert "never for a block that merely has an open comment" in CODRAFT_PROMPT


# ------------------------------------------------------------------ 6. tokens
class FakePages:
    def __init__(self, tmp_path):
        self.dir, self.n, self.content = tmp_path, 0, {1: b"page one", 2: b"page two"}

    def call(self, value):
        return value

    def screenshot_pages(self, doc_id, pages=None, max_pages=4, preview=False):
        shots = []
        for p, data in self.content.items():
            self.n += 1
            path = self.dir / f"{self.n}-p{p}.jpg"
            path.write_bytes(data)
            shots.append({"page": p, "path": str(path), "total": len(self.content), "more": [], "blocks": []})
        return shots


def test_a_page_that_did_not_change_is_not_sent_again(store, cfg_for, monkeypatch, tmp_path):
    d = store.create_document("Doc")
    fake = FakePages(tmp_path)
    monkeypatch.setattr(render, "get_renderer", lambda: fake)
    cfg = cfg_for(d["id"])
    begin_turn(d["id"], "t1")
    text, art = view_pages.func(config=cfg)
    assert len(art["images"]) == 2 and "not attached again" not in text
    fake.content[2] = b"page two, changed"
    text, art = view_pages.func(config=cfg)
    assert [p.split("-p")[1] for p in art["images"]] == ["2.jpg"]
    assert "Page 1 looks exactly as when you last viewed it this turn" in text
    text, art = view_pages.func(config=cfg)
    assert art is None and "Pages 1, 2 look exactly as" in text and "looking again will not help" in text
    begin_turn(d["id"], "t2")  # a new turn: the model no longer has those images
    assert len(view_pages.func(config=cfg)[1]["images"]) == 2


def test_pages_are_always_sent_outside_a_turn(store, cfg_for, monkeypatch, tmp_path):
    """An MCP client or a bring-your-own graph has no turn: nothing is held back."""
    d = store.create_document("Doc")
    monkeypatch.setattr(render, "get_renderer", lambda: FakePages(tmp_path))
    tools._TURNS.pop(d["id"], None)
    for _ in range(2):
        assert len(view_pages.func(config=cfg_for(d["id"]))[1]["images"]) == 2


def _thread():
    page = "Fetched page text. " * 400
    outline = "Document outline line.\n" * 60
    return [
        HumanMessage("Draft it.", id="h1"),
        AIMessage("", tool_calls=[{"name": "fetch_url", "args": {"url": "https://example.org"}, "id": "c1"},
                                  {"name": "get_document", "args": {}, "id": "c2"},
                                  {"name": "propose_edit", "args": {}, "id": "c3"}]),
        ToolMessage(page, tool_call_id="c1", name="fetch_url"),
        ToolMessage(outline, tool_call_id="c2", name="get_document"),
        ToolMessage("Suggestion sug_1 (edit) is waiting.", tool_call_id="c3", name="propose_edit"),
        AIMessage("Drafted."),
        HumanMessage("Now tighten it.", id="h2"),
        AIMessage("", tool_calls=[{"name": "fetch_url", "args": {"url": "https://example.org/2"}, "id": "c4"}]),
        ToolMessage(page, tool_call_id="c4", name="fetch_url"),
    ]


def test_earlier_turns_bulky_results_are_sent_trimmed():
    """Docs A and B: fetched pages from earlier turns were 30-40% of every later call."""
    msgs = _thread()
    out = _trim_old_turns(msgs)
    assert len(out) == len(msgs) and msgs[2].content.startswith("Fetched page text. Fetched")  # thread untouched
    assert len(out[2].content) < 500 and out[2].content.startswith("Fetched page text.")
    assert "you read this in an earlier turn" in out[2].content and out[2].tool_call_id == "c1"
    assert out[3].content.startswith("[get_document result from an earlier turn") and "Call get_document again" in out[3].content
    assert out[4] is msgs[4] and out[5] is msgs[5]  # short results and the agent's words stay
    assert out[8] is msgs[8] and out[7] is msgs[7]  # this turn is untouched
    assert [m.content for m in _trim_old_turns(msgs)] == [m.content for m in out]  # the same cut every call


def test_the_middleware_trims_unless_told_not_to(store):
    class Req:
        system_message = None

        def __init__(self, messages):
            self.messages = messages

        def override(self, **kw):
            return kw

    kw = CodraftMiddleware(attach_images=False, turn_notes=False)._prepare(Req(_thread()))
    assert len(kw["messages"][2].content) < 500
    kw = CodraftMiddleware(attach_images=False, turn_notes=False, trim_history=False)._prepare(Req(_thread()))
    assert "messages" not in kw


def test_each_author_message_starts_a_new_turn(store):
    d = store.create_document("Doc")
    cfg = {"configurable": {"thread_id": d["id"]}}
    msgs = _thread()
    _start_turn(cfg, msgs[:3])
    tools.turn_state(d["id"])["n"]["fetch_failed"] = 4
    _start_turn(cfg, msgs[:5])  # the same turn, a later model call
    assert tools.turn_state(d["id"])["n"]["fetch_failed"] == 4
    _start_turn(cfg, msgs)  # the author spoke again
    assert tools.turn_state(d["id"])["n"] == {}
