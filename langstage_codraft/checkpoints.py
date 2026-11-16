"""Document checkpoints: what the content was at a point in time (gh #61).

Every block has a version tree, and a deleted block keeps its tree, so nothing a
checkpoint needs is ever copied: a checkpoint is the list of ``[block id, the version
that was its head, its position]`` for the blocks that were in the document, in reading
order. That list is taken at the moment itself, under the store's lock, so it is right by
construction: a checkout moves a block's head without writing a version, and no amount of
reading version timestamps afterwards could tell which version was showing.

From it the store can say what changed since (edited, added, deleted, moved) and put the
content back, all of it or chosen blocks, without overwriting anything: an edited block
checks out the version it had (newer versions stay in its tree), a deleted block comes
back with its history, a block added since goes to Recently deleted, and the order is
restored. A restore takes a checkpoint of its own first, so it can be undone.

Checkpoints are content only. The look (settings, block layouts, reserved areas) has
its own history (``design_history.py``, gh #54), and reserved areas (``spacer`` blocks)
belong to it, so they are never part of a checkpoint.

This module is the pure part: names, which blocks count as moved, and a word diff short
enough to hand to a model. The checkpoints themselves live in the store
(``Store.create_checkpoint``, ``Store.checkpoint_changes``, ``Store.restore_checkpoint``).
"""
from __future__ import annotations

import re
from bisect import bisect_left
from difflib import SequenceMatcher

# Automatic checkpoints kept per document (named ones are kept until deleted).
AUTO_CAP = 100
# The most named checkpoints a document can hold; the next one is refused, not dropped.
NAMED_CAP = 200
NAME_MAX = 80
# An agent with no turn to group its proposals by (an MCP client): one checkpoint per burst.
AGENT_FOLD_S = 120.0

# Why an automatic checkpoint was taken.
REASONS = ("turn", "accept-all", "restore")


def clean_name(name: object) -> str:
    """A checkpoint's name as stored: one line, trimmed, at most ``NAME_MAX`` characters."""
    return re.sub(r"\s+", " ", str(name or "")).strip()[:NAME_MAX]


def auto_label(reason: str, detail: str = "", agent_name: str = "the agent") -> str:
    """What an automatic checkpoint is called: the thing it was taken just before."""
    if reason == "accept-all":
        n = int(detail) if str(detail).isdigit() else 0
        return f"Before Accept all ({n} suggestion{'s' if n != 1 else ''})" if n else "Before Accept all"
    if reason == "restore":
        return f"Before going back to “{detail}”" if detail else "Before undoing a restore"
    return f"Before {agent_name}'s turn"


def moved_ids(then: list[str], now: list[str]) -> list[str]:
    """The blocks that changed place: the smallest set that, taken out of both orders,
    leaves the rest in the same order (so swapping two neighbours counts one block, not
    two, and inserting or deleting a block moves nothing). ``then`` and ``now`` are the
    same ids in the old and the new order. Returned in the old order."""
    pos = {bid: i for i, bid in enumerate(then)}
    seq = [pos[b] for b in now if b in pos]
    # Longest increasing subsequence of the old positions, read in the new order: those
    # blocks kept their relative order. Patience sorting, with back-pointers.
    tails: list[int] = []      # tails[k] = index into seq of the smallest tail of a run of length k+1
    prev: list[int] = [-1] * len(seq)
    keys: list[int] = []
    for i, v in enumerate(seq):
        k = bisect_left(keys, v)
        if k == len(keys):
            keys.append(v)
            tails.append(i)
        else:
            keys[k] = v
            tails[k] = i
        prev[i] = tails[k - 1] if k else -1
    keep: set[int] = set()
    i = tails[-1] if tails else -1
    while i >= 0:
        keep.add(seq[i])
        i = prev[i]
    return [b for b in then if b in pos and pos[b] not in keep and b in set(now)]


def word_diff(before: str, after: str, budget: int = 420, context: int = 6) -> str:
    """A word-level diff in one line, for a model to read: removed words as ``[-…-]``,
    added ones as ``{+…+}``, with a few unchanged words around each change and ``…`` for
    the rest. At most about ``budget`` characters."""
    a, b = before.split(), after.split()
    if a == b:
        return ""
    out: list[str] = []
    ops = SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
    for n, (tag, i1, i2, j1, j2) in enumerate(ops):
        if tag == "equal":
            words = a[i1:i2]
            first, last = n == 0, n == len(ops) - 1
            if len(words) <= context * 2 and not first and not last:
                out.append(" ".join(words))
            else:
                head = [] if first else words[:context]
                tail = [] if last else words[-context:]
                out.append(" ".join(filter(None, [" ".join(head), "…", " ".join(tail)])))
            continue
        if i2 > i1:
            out.append("[-" + " ".join(a[i1:i2]) + "-]")
        if j2 > j1:
            out.append("{+" + " ".join(b[j1:j2]) + "+}")
    text = " ".join(p for p in out if p)
    if len(text) > budget:
        text = text[:budget].rsplit(" ", 1)[0] + " … (more changes: read the block)"
    return text
