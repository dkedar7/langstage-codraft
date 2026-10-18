"""langstage-codraft: the document stage for your LangGraph agent.

Bring your own agent::

    from langchain.agents import create_agent
    from langstage_codraft import CodraftMiddleware

    graph = create_agent("openai:gpt-4.1-mini", middleware=[CodraftMiddleware()])

or a factory Codraft calls with its tools and middleware::

    def make(tools, middleware):
        return create_agent(my_model, tools=[*tools, *my_tools], middleware=middleware)

then ``langstage-codraft run --agent my_agent.py:graph`` (or ``:make``).
"""
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("langstage-codraft")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0+local"


def __getattr__(name: str):
    # Lazy so `import langstage_codraft` (and the CLI's --version) stays fast.
    if name == "CODRAFT_TOOLS":
        from .tools import CODRAFT_TOOLS

        return CODRAFT_TOOLS
    if name == "CodraftMiddleware":
        from .middleware import CodraftMiddleware

        return CodraftMiddleware
    if name == "CODRAFT_PROMPT":
        from .middleware import CODRAFT_PROMPT

        return CODRAFT_PROMPT
    raise AttributeError(name)


__all__ = ["CODRAFT_PROMPT", "CODRAFT_TOOLS", "CodraftMiddleware", "__version__"]
