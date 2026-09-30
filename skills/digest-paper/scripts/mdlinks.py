"""Find image links in one line of Markdown, without regular expressions.

Shared by prepare_input.py and bundle.py so the input and the output accept the
same links. Handles what the pdf-mistral plugin writes and what people type:

  ![[vault/path.png|500]]            Obsidian embed        -> ("wiki", target)
  ![alt](file:///a/fig%20(x).png)    link destination       -> ("markdown", target)
  ![alt](<path with spaces.png>)     angle-bracket destination
  ![alt](a\\)b.png "title")           escaped parenthesis, optional title

Parentheses in a destination may nest to any depth (Node's pathToFileURL, which
the plugin uses, leaves "(" and ")" unencoded, so "figure (poly(NIPAm)).png"
occurs). A "![" that does not form a complete link is returned as
("broken", text) so the caller can report it instead of silently losing a
figure.
"""
from __future__ import annotations


def _destination(line: str, start: int) -> tuple[str, int] | None:
    """Parse the destination (and optional title) that starts after "(".

    Returns (destination, index after the closing ")") or None."""
    i, n = start, len(line)
    while i < n and line[i] in " \t":
        i += 1
    if i < n and line[i] == "<":
        end = line.find(">", i + 1)
        if end < 0:
            return None
        dest, i = line[i + 1:end], end + 1
    else:
        depth, chars = 0, []
        while i < n:
            ch = line[i]
            if ch == "\\" and i + 1 < n and line[i + 1] in "()\\":
                chars.append(line[i + 1])
                i += 2
                continue
            if ch == "(":
                depth += 1
            elif ch == ")":
                if depth == 0:
                    break
                depth -= 1
            elif ch in " \t" and depth == 0:
                break
            chars.append(ch)
            i += 1
        dest = "".join(chars)
    while i < n and line[i] in " \t":
        i += 1
    if i < n and line[i] in "\"'":
        close = line.find(line[i], i + 1)
        if close < 0:
            return None
        i = close + 1
        while i < n and line[i] in " \t":
            i += 1
    if i >= n or line[i] != ")" or not dest:
        return None
    return dest, i + 1


def image_links(line: str) -> list[tuple[str, str, str]]:
    """Every image link in the line as (kind, target, source text)."""
    found = []
    i = 0
    while True:
        start = line.find("![", i)
        if start < 0:
            return found
        if line.startswith("![[", start):
            end = line.find("]]", start + 3)
            if end < 0:
                found.append(("broken", line[start:start + 120], line[start:start + 120]))
                return found
            found.append(("wiki", line[start + 3:end], line[start:end + 2]))
            i = end + 2
            continue
        # ![alt](...): the alt text may contain balanced brackets.
        j, depth = start + 2, 1
        while j < len(line) and depth:
            if line[j] == "\\":
                j += 2
                continue
            depth += {"[": 1, "]": -1}.get(line[j], 0)
            j += 1
        if depth or j >= len(line) or line[j] != "(":
            found.append(("broken", line[start:start + 120], line[start:start + 120]))
            i = start + 2
            continue
        parsed = _destination(line, j + 1)
        if parsed is None:
            found.append(("broken", line[start:start + 120], line[start:start + 120]))
            i = j + 1
            continue
        dest, end = parsed
        found.append(("markdown", dest, line[start:end]))
        i = end
