"""Command output parser — real ``command → output`` samples from uploaded session logs.

Works on any interactive shell transcript (UC- and vendor-agnostic): a line that starts
with a shell prompt followed by text is a command; everything up to the next prompt line
is its output. Recognised prompt shapes include::

    rdsggn01:~ # <cmd>        host:path #|$|>
    rdsggn01$ <cmd>           host$ / host#
    [user@host ~]$ <cmd>      bracketed user@host
    user@host:~> <cmd>        user@host:path>

Downstream steps use the samples to check that generated validation/register regexes
match what the real node prints (spec 004 DEP-5), and the simulator can reuse them as
realistic responses.
"""
from __future__ import annotations

import re
from typing import Any

MAX_OUTPUT_CHARS = 6000
MAX_SAMPLES = 400

_PROMPT = (r"(?:\[[^\]\s][^\]]*\]"                      # [user@host path]
           r"|[\w.-]+@[\w.-]+(?::\S*)?"                  # user@host[:path]
           r"|[\w.-]+:\S*"                               # host:path
           r"|[\w.-]+)"                                  # host
           r"\s?[#$>%]")
_PROMPT_LINE = re.compile(rf"^(?P<prompt>{_PROMPT})(?:\s+(?P<cmd>\S.*))?\s*$")
_FILE_SEPARATOR = re.compile(r"^=== .+ ===$")            # files joined by the requirements reader


def parse_command_outputs(text: str) -> list[dict[str, Any]]:
    """Return ``[{"command", "output", "prompt"}]`` in transcript order.

    Commands without output are kept (``output == ""``) — they still prove the exact
    command text. Outputs are trimmed of trailing blank lines and capped in size.
    """
    if not text:
        return []
    samples: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    body: list[str] = []

    def close() -> None:
        if current is None:
            return
        while body and not body[-1].strip():
            body.pop()
        current["output"] = "\n".join(body)[:MAX_OUTPUT_CHARS]
        samples.append(current)

    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        m = _PROMPT_LINE.match(line)
        if _FILE_SEPARATOR.match(line) or (m and _looks_like_prompt(m.group("prompt"))):
            close()
            current, body = None, []
            if m and m.group("cmd") and not _FILE_SEPARATOR.match(line):
                current = {"command": m.group("cmd").strip(), "prompt": m.group("prompt"), "output": ""}
            if len(samples) >= MAX_SAMPLES:
                break
            continue
        if current is not None:
            body.append(line)
    close()
    return samples[:MAX_SAMPLES]


def _looks_like_prompt(prompt: str) -> bool:
    """Reject output lines that merely end with a prompt character (e.g. "Total: 5 >")."""
    head = prompt[:-1].strip()
    if not head or len(head) > 80:
        return False
    # bare words like "Average" or "Status" are not hostnames-with-prompt unless the
    # prompt char directly follows them (host$ / host#)
    return bool(re.search(r"[@:\[\]~]", head)) or prompt.rstrip()[-1] in "$#" and " " not in head
