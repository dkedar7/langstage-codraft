"""Resolve which agent a Codraft server runs.

Three ways to bring an agent (``--agent path.py:obj``):

* **a compiled graph** that already carries the tools, e.g.
  ``create_agent(model, middleware=[CodraftMiddleware()])``. It is used as-is.
* **a factory** ``def make(tools, middleware): ...``. Codraft calls it with
  ``CODRAFT_TOOLS`` and ``[CodraftMiddleware()]``: the deployer builds the graph and
  Codraft adds what this app needs (notes p.4).
* **a compiled graph without the tools.** It still chats, but it cannot touch the
  document. ``langstage-codraft check`` reports this.

Without ``--agent``, a default agent is built from ``--model`` or ``[codraft] model``
(or the first API key found). ``--demo`` runs a keyless scripted co-author that
exercises the whole suggestion flow.
"""
from __future__ import annotations

import inspect
import json
import os
import re
import uuid
from typing import Any

from langchain_core.runnables import RunnableConfig

from .middleware import CodraftMiddleware
from .tools import CODRAFT_TOOLS, TOOL_NAMES

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-image"


# ------------------------------------------------------------------------- models
def default_model_spec() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return f"openrouter:{DEFAULT_OPENROUTER_MODEL}"
    if os.environ.get("OPENAI_API_KEY"):
        return f"openai:{DEFAULT_OPENAI_MODEL}"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-haiku-5-5"
    return None


def make_model(spec: str) -> Any:
    """``openrouter:<slug>`` or any ``init_chat_model`` spec (``openai:gpt-4.1``…)."""
    if spec.startswith("openrouter:"):
        from langchain_openai import ChatOpenAI

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set (needed for an openrouter: model)")
        return ChatOpenAI(model=spec.split(":", 1)[1], base_url=OPENROUTER_BASE, api_key=key,
                          temperature=0.3, default_headers={"X-Title": "langstage-codraft"})
    from langchain.chat_models import init_chat_model

    try:
        return init_chat_model(spec)
    except ImportError as exc:
        provider = spec.split(":", 1)[0]
        hint = ' (pip install "langstage-codraft[anthropic]")' if provider == "anthropic" else ""
        raise ImportError(f"model {spec!r} needs the langchain-{provider} package{hint}: {exc}") from exc


def research_tools() -> list:
    """Keyless research helpers for the default agent: fetch a URL, and web search
    when the optional ``ddgs`` package (``[search]`` extra) is installed."""
    from langchain_core.tools import tool

    @tool
    def fetch_url(url: str, max_chars: int = 6000) -> str:
        """Fetch a web page and return its readable text (truncated)."""
        import httpx

        try:
            r = httpx.get(url, timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (langstage-codraft)"})
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return f"ERROR fetching {url}: {exc}"
        from .textutil import extract_text

        text = extract_text("page.html", r.content) if "html" in r.headers.get("content-type", "") else r.text
        return text[: max(500, min(max_chars, 20000))]

    tools = [fetch_url]
    if os.environ.get("OPENROUTER_API_KEY"):
        tools.append(generate_image_tool())
    try:
        from ddgs import DDGS

        @tool
        def web_search(query: str, max_results: int = 5) -> str:
            """Search the web. Returns titles, urls and snippets to cite as references."""

            try:
                hits = list(DDGS().text(query, max_results=max(1, min(max_results, 10))))
            except Exception as exc:  # noqa: BLE001
                return f"ERROR searching: {exc}"
            return "\n".join(f"- {h.get('title')} — {h.get('href')}\n  {h.get('body')}" for h in hits) or "No results."

        tools.append(web_search)
    except ImportError:
        pass
    return tools


def generate_image_tool():
    """An image-generation tool (OpenRouter image model): turns a prompt, and optionally the
    author's sketch, into an image on an existing image block or a new one."""
    from langchain_core.tools import tool

    @tool
    def generate_image(
        prompt: str, config: RunnableConfig, block_id: str | None = None, after_block_id: str | None = None,
        aspect_ratio: str = "4:3", use_sketch: bool = True, label: str = "", title: str = "",
        source: str = "", caption: str = "", full_width: bool = False, figure_box: bool = False,
    ) -> str:
        """Generate a photo-like image or an illustration (hero art, cover art, a product
        shot, a portrait-style illustration, a screenshot-style mock-up). Describe subject,
        style, palette and composition. It fills `block_id` (an image block; the author's
        sketch on it guides the composition when use_sketch=True) or becomes a new image
        block after `after_block_id`. aspect_ratio is one of 1:1, 3:2, 2:3, 4:3, 3:4, 16:9,
        9:16, 21:9. For diagrams with readable labels, prefer draw_figure (SVG)."""
        import base64
        import re as _re

        import httpx

        from .store import get_store
        from .tools import _doc_id, _sketch_path, _sugg_result

        try:
            doc_id = _doc_id(config)
            store = get_store()
            sk = None
            if block_id:
                b = store.get_block(block_id)
                if b["type"] != "image":
                    return f"ERROR: {block_id} is a {b['type']} block, not an image block"
                sk = _sketch_path(doc_id, block_id) if use_sketch else None
            parts: list[dict] = [{"type": "text", "text": (
                "Create a clean, publication-quality image for a printed report. " + prompt
                + (" Follow the composition of the attached sketch." if sk else "")
                + " No watermarks, no captions, no borders.")}]
            if sk:
                b64 = base64.b64encode(open(sk, "rb").read()).decode()
                parts.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
            ratio = aspect_ratio if _re.fullmatch(r"\d{1,2}:\d{1,2}", aspect_ratio or "") else "4:3"
            r = httpx.post(f"{OPENROUTER_BASE}/chat/completions", timeout=180, headers={
                "Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "X-Title": "langstage-codraft"},
                json={"model": os.environ.get("LANGSTAGE_CODRAFT_IMAGE_MODEL", DEFAULT_IMAGE_MODEL),
                      "modalities": ["image", "text"], "image_config": {"aspect_ratio": ratio},
                      "messages": [{"role": "user", "content": parts}]})
            r.raise_for_status()
            msg = r.json()["choices"][0]["message"]
            urls = [i.get("image_url", {}).get("url", "") for i in (msg.get("images") or [])]
            data_url = next((u for u in urls if u.startswith("data:image")), None)
            if not data_url:
                return f"ERROR: the image model returned no image ({(msg.get('content') or '')[:200]})"
            mime, b64data = _re.match(r"data:(image/[\w+.-]+);base64,(.*)", data_url, _re.S).groups()
            ext = mime.split("/")[1].replace("jpeg", "jpg")
            up = store.add_upload(doc_id, f"generated-{block_id or 'new'}.{ext}", base64.b64decode(b64data),
                                  purpose="asset", mime=mime)
            content = {"src": up["id"], "alt": prompt[:120]}
            for k, v in (("label", label), ("title", title), ("source", source), ("caption", caption)):
                if v:
                    content[k] = v
            variant = {"variant": "figure-box"} if figure_box else {}
            note = f"Generated image: {prompt[:80]}"
            if block_id:
                s = store.propose(doc_id, "edit", {"content": content, **({"layout": variant} if variant else {})},
                                  block_id=block_id, note=note)
            else:
                s = store.propose(doc_id, "insert", {
                    "type": "image", "content": content, "after": after_block_id or "__end__",
                    "layout": {"span": "full" if full_width else "column", **variant}}, note=note)
            return f"Generated image {up['id']} ({ratio}). " + _sugg_result(s)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR generating image: {type(exc).__name__}: {exc}"

    return generate_image


def build_default_agent(model_spec: str, *, extra_tools: list | None = None) -> Any:
    from langchain.agents import create_agent

    return create_agent(
        make_model(model_spec),
        tools=research_tools() + list(extra_tools or []),
        middleware=[CodraftMiddleware()],
        name="codraft",
    )


# --------------------------------------------------------------------- inspection
def graph_tool_names(graph: Any) -> set[str]:
    """Best-effort: the tool names a compiled graph's tool nodes can call."""
    names: set[str] = set()
    for node in (getattr(graph, "nodes", None) or {}).values():
        for obj in (node, getattr(node, "bound", None), getattr(node, "runnable", None)):
            tbn = getattr(obj, "tools_by_name", None)
            if isinstance(tbn, dict):
                names |= set(tbn)
    return names


def is_graph(obj: Any) -> bool:
    return hasattr(obj, "aget_state") and hasattr(obj, "nodes")


def resolve_agent_object(obj: Any) -> tuple[Any, str]:
    """Turn a loaded spec object into (graph, kind)."""
    if is_graph(obj):
        return obj, "graph"
    if callable(obj):
        params = inspect.signature(obj).parameters
        kwargs: dict[str, Any] = {}
        if "tools" in params:
            kwargs["tools"] = list(CODRAFT_TOOLS)
        if "middleware" in params:
            kwargs["middleware"] = [CodraftMiddleware(tools=[])] if "tools" in params else [CodraftMiddleware()]
        g = obj(**kwargs)
        if not is_graph(g):
            raise TypeError(f"agent factory returned {type(g).__name__}, not a compiled LangGraph graph")
        return g, "factory"
    raise TypeError(f"agent spec resolved to {type(obj).__name__}; expected a compiled graph or a factory")


def missing_tools(graph: Any) -> list[str]:
    have = graph_tool_names(graph)
    return sorted(TOOL_NAMES - have)


# --------------------------------------------------------------------- demo agent
def _last_human_idx(messages: list) -> int:
    for i in range(len(messages) - 1, -1, -1):
        if getattr(messages[i], "type", "") == "human":
            return i
    return -1


def _call(name: str, args: dict) -> dict:
    return {"name": name, "args": args, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "tool_call"}


def _demo_revise(block_text: str, comment: str) -> str:
    c = comment.lower()
    text = block_text.strip()
    if any(w in c for w in ("short", "concise", "tighten", "trim")):
        sentences = re.split(r"(?<=[.!?])\s+", text)
        if len(sentences) > 1:
            return " ".join(sentences[: max(1, len(sentences) // 2)])
        words = text.split()
        return " ".join(words[: max(4, len(words) * 2 // 3)]).rstrip(",;:") + "."
    if any(w in c for w in ("expand", "more detail", "elaborate", "longer")):
        return text + "\n\nThis point deserves more detail: it shapes the reader's next decision, so it is spelled out here."
    if any(w in c for w in ("formal", "professional")):
        return text.replace("can't", "cannot").replace("don't", "do not").replace("it's", "it is")
    return text + f"\n\n*Revised to address: “{comment.strip()}”.*"


def demo_turn(messages: list) -> dict:
    """The scripted co-author: returns {"content": str, "tool_calls": [...]}.

    Deterministic and keyless. It follows the same tool path a real model would, so
    the demo exercises the suggestion/approval flow end to end."""
    i = _last_human_idx(messages)
    human = messages[i] if i >= 0 else None
    text = human.content if human is not None and isinstance(human.content, str) else ""
    low = text.lower()
    tool_msgs = [m for m in messages[i + 1:] if getattr(m, "type", "") == "tool"]
    done = {m.name for m in tool_msgs}
    last = tool_msgs[-1] if tool_msgs else None

    if last is None:
        if "comment" in low:
            return {"content": "Let me look at the open comments, top to bottom.",
                    "tool_calls": [_call("list_comments", {})]}
        if any(w in low for w in ("layout", "look at", "view", "pages")):
            return {"content": "", "tool_calls": [_call("view_pages", {})]}
        if "reference" in low or "sources" in low:
            return {"content": "", "tool_calls": [_call("compile_references", {})]}
        if any(low.startswith(w) for w in ("write", "draft", "add")):
            topic = re.sub(r"^(write|draft|add)\b(.*?\b(about|on)\b)?\s*(a|an|the)?\s*", "", text,
                           flags=re.IGNORECASE).strip().rstrip(".") or "this topic"
            body = (f"On {topic}: this matters because it changes what the reader can do next. "
                    "This draft states the claim, gives one piece of evidence, and closes with the implication.")
            return {"content": "", "tool_calls": [_call("propose_insert", {
                "block_type": "text", "content": body, "note": f"Draft paragraph: {topic[:60]}"})]}
        return {"content": "", "tool_calls": [_call("get_document", {})]}

    if last.name == "list_comments":
        found = re.findall(r"\[(cmt_\w+)\] \(open\) block \[(blk_\w+)\].*? — \w+: (.*)", last.content)
        if not found:
            return {"content": "There are no open comments on blocks, so there is nothing to address.", "tool_calls": []}
        return {"content": "", "tool_calls": [_call("read_blocks", {"block_ids": [b for _, b, _ in found]})]}

    if last.name == "read_blocks" and "propose_edit" not in done:
        lc = next((m for m in tool_msgs if m.name == "list_comments"), None)
        found = re.findall(r"\[(cmt_\w+)\] \(open\) block \[(blk_\w+)\].*? — \w+: (.*)", lc.content if lc else "")
        bodies: dict[str, str] = {}
        for chunk in re.split(r"\n\n(?=\[blk_)", last.content):
            m = re.match(r"\[(blk_\w+)\] (\w[\w-]*) [^\n]*\n(.*)", chunk, re.DOTALL)
            if m:
                bodies[m.group(1)] = (m.group(2), re.sub(r"\n  ref: .*", "", m.group(3)).strip())
        calls = []
        for cid, bid, body in found:
            btype, current = bodies.get(bid, ("text", ""))
            if btype not in ("text", "callout", "heading"):
                calls.append(_call("reply_to_comment", {
                    "comment_id": cid,
                    "message": f"The demo agent can only revise text; this is a {btype} block, so there's no tool for it."}))
                continue
            new = current.lstrip("# ").title() if btype == "heading" else _demo_revise(current, body)
            calls.append(_call("propose_edit", {"block_id": bid, "content": new, "comment_id": cid,
                                                "note": f"Addresses: {body.strip()[:80]}"}))
            calls.append(_call("reply_to_comment", {"comment_id": cid,
                                                    "message": "Proposed a revision; accept or reject it on the block."}))
        return {"content": "", "tool_calls": calls}

    if last.name in ("propose_edit", "reply_to_comment"):
        n = sum(1 for m in tool_msgs if m.name == "propose_edit")
        return {"content": f"Done. I proposed {n} edit(s), handling the comments from top to bottom. "
                           "Each one is waiting on its block for you to accept or reject.", "tool_calls": []}
    if last.name == "propose_insert":
        return {"content": "I drafted a paragraph as a suggestion at the end of the document. Accept it to keep it.",
                "tool_calls": []}
    if last.name == "view_pages":
        return {"content": "I looked at the rendered pages. (The demo agent can't judge images; a vision model "
                           "would comment on spacing and overflow here.)", "tool_calls": []}
    if last.name == "compile_references":
        return {"content": "Here are the sources cited so far:\n\n" + last.content, "tool_calls": []}
    if last.name == "get_document":
        n = len(re.findall(r"^\d+\. \[blk_", last.content, re.MULTILINE))
        return {"content": (f"(demo agent) I can see {n} block(s). Try: **address all comments**, "
                            "**write a paragraph about <topic>**, **look at the layout**, or **list the references**."),
                "tool_calls": []}
    return {"content": "(demo agent) Done.", "tool_calls": []}


def build_demo_agent() -> Any:
    from langchain.agents import create_agent
    from langchain_core.language_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class DemoCoauthorModel(BaseChatModel):
        """Keyless scripted model (see :func:`demo_turn`)."""

        @property
        def _llm_type(self) -> str:
            return "codraft-demo"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            turn = demo_turn(list(messages))
            msg = AIMessage(content=turn["content"], tool_calls=turn["tool_calls"])
            return ChatResult(generations=[ChatGeneration(message=msg)])

    return create_agent(DemoCoauthorModel(), middleware=[CodraftMiddleware(attach_images=False)],
                        name="codraft-demo")


# ---------------------------------------------------------------------- resolution
def resolve(cfg: Any, *, model: str | None = None) -> tuple[Any, dict]:
    """(graph, info) for a resolved CodraftConfig. ``info`` feeds ``check`` and the UI."""
    from langstage_core import load_agent_spec

    if cfg.demo:
        return build_demo_agent(), {"kind": "demo", "label": "Demo co-author (keyless)", "missing_tools": []}
    if cfg.agent_spec:
        obj = load_agent_spec(cfg.agent_spec, base_dir=cfg.toml_dir_for("agent_spec"))
        graph, kind = resolve_agent_object(obj)
        return graph, {"kind": kind, "label": cfg.agent_spec, "missing_tools": missing_tools(graph)}
    spec = model or cfg.model or default_model_spec()
    if not spec:
        return None, {"kind": "none", "label": "No agent configured",
                      "missing_tools": sorted(TOOL_NAMES),
                      "hint": "Pass --agent path.py:graph, --model provider:model, set OPENROUTER_API_KEY "
                              "or OPENAI_API_KEY, or run with --demo."}
    return build_default_agent(spec), {"kind": "default", "label": spec, "missing_tools": []}


def describe_info(info: dict) -> str:
    return json.dumps(info, indent=2)
