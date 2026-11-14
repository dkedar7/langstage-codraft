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
import logging
import os
import re
import uuid
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig

from .middleware import CodraftMiddleware
from .tools import CODRAFT_TOOLS, TOOL_NAMES

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_OPENROUTER_MODEL = "anthropic/claude-haiku-5.5"
DEFAULT_OPENAI_MODEL = "gpt-5-mini"
DEFAULT_IMAGE_MODEL = "google/gemini-3.1-flash-image"
# The most the default agent may write in one model call (reasoning included). Without a
# cap some gateways reserve the model's whole output window up front (OpenRouter: 65,536
# tokens for a Claude model), and a key with little credit fails before the first token.
DEFAULT_MAX_OUTPUT_TOKENS = 16000
REASONING_LEVELS = ("auto", "off", "low", "medium", "high")
_AUTO_EFFORT = "low"  # what "auto" asks for where the model can reason

_log = logging.getLogger("langstage_codraft.agent")


# ------------------------------------------------------------------------- models
def default_model_spec() -> str | None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return f"openrouter:{DEFAULT_OPENROUTER_MODEL}"
    if os.environ.get("OPENAI_API_KEY"):
        return f"openai:{DEFAULT_OPENAI_MODEL}"
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic:claude-haiku-5-5"
    return None


def reasoning_kwargs(spec: str, reasoning: str = "auto", max_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS) -> dict:
    """The constructor arguments that turn reasoning on for this model, or {} when it is
    off or this provider has no switch Codraft knows. Reasoning is what keeps a model's
    arithmetic and multi-step edits right; see ``[codraft] reasoning``."""
    effort = _AUTO_EFFORT if reasoning == "auto" else reasoning
    if effort not in ("low", "medium", "high"):
        return {}
    provider, _, name = spec.partition(":")
    if provider == "openrouter":
        # OpenRouter's unified switch; it is ignored for models that can't reason.
        return {"extra_body": {"reasoning": {"effort": effort}}}
    if provider == "openai" and re.match(r"(gpt-5|o\d)", name):
        # The Responses API: GPT-5.4 and later only reason together with tools there (Chat
        # Completions refuses `reasoning_effort` with function tools). It needs
        # langstage-core 1.0.64, which no longer repeats the final reply on this API. If an
        # account or a gateway refuses the Responses API, the fallback below carries on
        # over plain Chat Completions without reasoning (see ReasoningFallback).
        return {"use_responses_api": True, "reasoning": {"effort": effort}}
    if provider == "anthropic":
        budget = {"low": 2048, "medium": 6000, "high": 16000}[effort]
        if max_tokens and max_tokens < budget + 1024:
            budget = max_tokens - 1024
        return {"thinking": {"type": "enabled", "budget_tokens": budget}} if budget >= 1024 else {}
    return {}


def _construct(spec: str, **kwargs: Any) -> Any:
    if spec.startswith("openrouter:"):
        from langchain_openai import ChatOpenAI

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise RuntimeError("OPENROUTER_API_KEY is not set (needed for an openrouter: model)")
        return ChatOpenAI(model=spec.split(":", 1)[1], base_url=OPENROUTER_BASE, api_key=key,
                          temperature=0.3, default_headers={"X-Title": "langstage-codraft"}, **kwargs)
    from langchain.chat_models import init_chat_model

    try:
        return init_chat_model(spec, **kwargs)
    except ImportError as exc:
        provider = spec.split(":", 1)[0]
        hint = ' (pip install "langstage-codraft[anthropic]")' if provider == "anthropic" else ""
        raise ImportError(f"model {spec!r} needs the langchain-{provider} package{hint}: {exc}") from exc


def make_models(spec: str, *, max_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS,
                reasoning: str = "auto") -> tuple[Any, Any, list[str]]:
    """``(model, plain, notes)`` for ``openrouter:<slug>`` or any ``init_chat_model`` spec.

    ``model`` has the output cap and, where the provider has a switch, reasoning on.
    ``plain`` is the same model without reasoning (None when reasoning isn't on), for
    :class:`ReasoningFallback`. A provider class that refuses one of these arguments still
    yields a model: the argument is dropped and ``notes`` says so."""
    notes: list[str] = []
    cap = {"max_tokens": int(max_tokens)} if max_tokens and int(max_tokens) > 0 else {}
    think = reasoning_kwargs(spec, reasoning, int(max_tokens or 0))
    if reasoning not in ("off", "auto") and not think:
        notes.append(f"reasoning={reasoning} has no effect: Codraft has no reasoning switch for {spec}")

    def build(**kw: Any) -> Any:
        try:
            return _construct(spec, **kw)
        except (ImportError, RuntimeError):
            raise
        except Exception as exc:  # noqa: BLE001 - a provider class that refuses an argument
            if not kw:
                raise
            notes.append(f"{spec} refused {', '.join(sorted(kw))} ({type(exc).__name__}); using the provider's defaults")
            return _construct(spec)

    plain = build(**cap)
    if not think:
        return plain, None, notes
    try:
        return _construct(spec, **cap, **think), plain, notes
    except (ImportError, RuntimeError):
        raise
    except Exception as exc:  # noqa: BLE001
        notes.append(f"reasoning is off: {spec} refused the setting ({type(exc).__name__})")
        return plain, None, notes


def make_model(spec: str, *, max_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS, reasoning: str = "auto") -> Any:
    """``openrouter:<slug>`` or any ``init_chat_model`` spec (``openai:gpt-4.1``…), with the
    output cap and reasoning (see :func:`make_models`)."""
    return make_models(spec, max_tokens=max_tokens, reasoning=reasoning)[0]


_REJECTS = re.compile(
    r"reasoning|thinking|effort|budget_tokens|unsupported param|unknown param|unrecognized (request )?arg"
    r"|extra_forbidden|extra inputs|not supported|does not support|unexpected keyword"
    r"|/responses\b|responses api", re.I)


def rejects_reasoning(exc: BaseException) -> bool:
    """A provider's "bad request" that names the reasoning setting (or an unsupported
    parameter, or the Responses API), as opposed to any other failure. A gateway that has no
    Responses API at all answers 404 or 405 for it, often with nothing but "Not Found"; that
    counts too, since the plain model then says what is really wrong if it is something else
    (an unknown model fails the same way on it)."""
    code = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    if code in (404, 405):
        return True
    if code not in (400, 422) and not isinstance(exc, (TypeError, ValueError)):
        return False
    return bool(_REJECTS.search(str(exc)))


class ReasoningFallback(AgentMiddleware):
    """Run the default agent with reasoning on, and without it if the provider refuses.

    Which models accept which reasoning setting changes faster than this package ships, so
    the setting is tried, not assumed: the first model call the provider rejects because of
    it is repeated on ``plain`` (the same model, reasoning off), and every later call goes
    straight to ``plain``. The agent never fails over a setting the author didn't choose."""

    def __init__(self, plain: Any, label: str = ""):
        super().__init__()
        self.plain = plain
        self.label = label
        self.degraded = ""  # why, once reasoning has been dropped

    def _degrade(self, exc: BaseException) -> None:
        self.degraded = f"{type(exc).__name__}: {exc}"[:300]
        _log.warning("codraft: %s refused the reasoning setting, continuing without it (%s). "
                     "Set [codraft] reasoning = \"off\" to skip the attempt.", self.label or "the model", self.degraded)

    def wrap_model_call(self, request, handler):  # type: ignore[override]
        if self.degraded:
            return handler(request.override(model=self.plain))
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            if not rejects_reasoning(exc):
                raise
            self._degrade(exc)
            return handler(request.override(model=self.plain))

    async def awrap_model_call(self, request, handler):  # type: ignore[override]
        if self.degraded:
            return await handler(request.override(model=self.plain))
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            if not rejects_reasoning(exc):
                raise
            self._degrade(exc)
            return await handler(request.override(model=self.plain))


def research_tools() -> list:
    """Keyless research helpers for the default agent: fetch a URL, and web search
    when the optional ``ddgs`` package (``[search]`` extra) is installed."""
    from langchain_core.tools import tool

    @tool
    def fetch_url(url: str, max_chars: int = 6000, offset: int = 0) -> str:
        """Fetch a web page or a PDF and return its readable text. Long documents are cut
        at max_chars; the result says how much is left, and `offset` continues from there."""
        import httpx

        try:
            r = httpx.get(url, timeout=20, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 (langstage-codraft)"})
            r.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            return (f"ERROR fetching {url}: {exc}. Do not cite this source unless you can open it another "
                    "way; say so if it matters.")
        return readable_text(url, r.headers.get("content-type", ""), r.content, max_chars=max_chars, offset=offset)

    tools = [fetch_url]
    if os.environ.get("OPENROUTER_API_KEY"):
        tools.append(generate_image_tool())
    try:
        from ddgs import DDGS

        @tool
        def web_search(query: str, max_results: int = 5) -> str:
            """Search the web. Returns titles, urls and snippets to cite as references."""

            import time

            n = max(1, min(max_results, 10))
            err: Exception | None = None
            for attempt in (0, 1):  # the search service rate-limits in bursts: one retry clears most
                try:
                    hits = list(DDGS().text(query, max_results=n))
                    break
                except Exception as exc:  # noqa: BLE001
                    err = exc
                    if attempt == 0:
                        time.sleep(1.5)
            else:
                return (f"ERROR: web search failed twice ({type(err).__name__}: {str(err)[:160]}). The search "
                        "service is rate-limited or unreachable right now. Do not keep retrying: use fetch_url on "
                        "a URL you already know, use the uploaded reference material (search_document), or tell "
                        "the author which claims you could not source. Never cite a page you have not opened.")
            return ("\n".join(f"- {h.get('title')} — {h.get('href')}\n  {h.get('body')}" for h in hits)
                    or f"No results for {query!r}. Try fewer or different keywords.")

        tools.append(web_search)
    except ImportError:
        pass
    return tools


def readable_text(url: str, content_type: str, data: bytes, *, max_chars: int = 6000, offset: int = 0) -> str:
    """What fetch_url returns for a response body: page text with entities decoded, a
    PDF's extracted text (never its raw bytes), cut to a window with a note on the rest."""
    import html as _html

    from .textutil import extract_text

    ctype = (content_type or "").lower()
    is_pdf = "pdf" in ctype or data[:5] == b"%PDF-" or url.lower().split("?")[0].endswith(".pdf")
    if is_pdf:
        text = extract_text("page.pdf", data)
        if not text or text.startswith("[could not extract"):
            return (f"{url} is a PDF whose text can't be read here (scanned, encrypted or damaged). Don't quote "
                    "from it; cite it only for what its title or abstract page (if you can open one) supports.")
        kind = "PDF"
    elif "html" in ctype or data[:200].lstrip().lower().startswith((b"<!doctype html", b"<html")):
        text, kind = _html.unescape(extract_text("page.html", data)).replace("\xa0", " "), "page"
    elif ctype.startswith(("text/", "application/json", "application/xml")) or not ctype:
        text, kind = data.decode("utf-8", "replace"), "text"
    else:
        return f"{url} is {ctype or 'a binary file'}, not text: nothing readable to return."
    size = max(500, min(int(max_chars), 20000))
    start = max(0, int(offset))
    part = text[start:start + size]
    left = len(text) - (start + len(part))
    more = f"\n[{left} more characters: call fetch_url again with offset={start + len(part)}]" if left > 0 else ""
    return (f"[{kind}, characters {start}-{start + len(part)} of {len(text)}]\n{part}{more}" if (start or left > 0)
            else part)


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


def build_default_agent(model_spec: str, *, extra_tools: list | None = None,
                        max_tokens: int | None = DEFAULT_MAX_OUTPUT_TOKENS, reasoning: str = "auto",
                        notes: list[str] | None = None) -> Any:
    """The built-in co-author on ``model_spec``: output capped at ``max_tokens``, reasoning
    on where the model has it (and dropped, not fatal, if the provider refuses it)."""
    from langchain.agents import create_agent

    model, plain, found = make_models(model_spec, max_tokens=max_tokens, reasoning=reasoning)
    if notes is not None:
        notes.extend(found)
    for n in found:
        _log.warning("codraft: %s", n)
    middleware: list = [CodraftMiddleware()]
    if plain is not None:
        middleware.append(ReasoningFallback(plain, model_spec))
    return create_agent(
        model,
        tools=research_tools() + list(extra_tools or []),
        middleware=middleware,
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


# An open comment on a block in list_comments output; authors are names, so may contain spaces.
_OPEN_COMMENT = r"\[(cmt_\w+)\] \(open\) block \[(blk_\w+)\].*? — [^:\n]+: (.*)"


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
        found = re.findall(_OPEN_COMMENT, last.content)
        if not found:
            return {"content": "There are no open comments on blocks, so there is nothing to address.", "tool_calls": []}
        return {"content": "", "tool_calls": [_call("read_blocks", {"block_ids": [b for _, b, _ in found]})]}

    if last.name == "read_blocks" and "propose_edit" not in done:
        lc = next((m for m in tool_msgs if m.name == "list_comments"), None)
        found = re.findall(_OPEN_COMMENT, lc.content if lc else "")
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
                            "**write a paragraph about tidal energy**, **look at the layout**, or **list the references**."),
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
    notes: list[str] = []
    cap = getattr(cfg, "max_output_tokens", DEFAULT_MAX_OUTPUT_TOKENS)
    reasoning = getattr(cfg, "reasoning", "auto")
    graph = build_default_agent(spec, max_tokens=cap, reasoning=reasoning, notes=notes)
    on = reasoning_kwargs(spec, reasoning, int(cap or 0))
    info = {"kind": "default", "label": spec, "missing_tools": [], "max_output_tokens": cap or None,
            "reasoning": (_AUTO_EFFORT if reasoning == "auto" else reasoning) if on else "off"}
    if notes:
        info["notes"] = notes
    return graph, info


def describe_info(info: dict) -> str:
    return json.dumps(info, indent=2)
