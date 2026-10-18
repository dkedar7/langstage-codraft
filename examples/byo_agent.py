"""Bring your own agent to Codraft.

    langstage-codraft run --agent examples/byo_agent.py:graph
    langstage-codraft run --agent examples/byo_agent.py:make

Needs OPENROUTER_API_KEY (or swap the model for any LangChain chat model).
"""
import os

from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

from langstage_codraft import CodraftMiddleware

model = ChatOpenAI(
    model="anthropic/claude-haiku-5.5",
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ.get("OPENROUTER_API_KEY", "missing"),
)


@tool
def company_glossary(term: str) -> str:
    """Look up an internal term so the agent uses it the way the company does."""
    glossary = {"pilot": "A time-boxed trial on a subset of tools with a go/no-go review."}
    return glossary.get(term.lower(), f"No glossary entry for {term!r}.")


# Form 1: a compiled graph. The middleware registers Codraft's tools and prompt.
graph = create_agent(model, tools=[company_glossary], middleware=[CodraftMiddleware()], name="acme_coauthor")


# Form 2: a factory. Codraft passes in its tools and middleware.
def make(tools, middleware):
    return create_agent(model, tools=[*tools, company_glossary], middleware=middleware, name="acme_coauthor")
