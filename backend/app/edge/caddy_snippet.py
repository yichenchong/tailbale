"""Caddy snippet-containment lexer (block-escape guard).

Load-bearing security code: this validator lexes the *rendered* form of a
user-supplied custom Caddy snippet exactly the way Caddy's caddyfile lexer
does, to guarantee the snippet cannot break OUT of its per-service site block.
It lives beside ``render_snippet_block`` (the renderer it guards) so the two
share a single source of truth: the validated bytes can never diverge from
what is rendered into the Caddyfile.

The lexer logic was converged over prior fuzz rounds and MUST NOT change.
"""

import re

from app.edge.config_renderer import render_snippet_block

# ``validate_caddy_snippet`` is the sole public entry point. The schema layer
# (``schemas/services.py``) depends on this single stable name, never on this
# module's internal lexer/renderer wiring, so keep it the only exported symbol.
__all__ = ["validate_caddy_snippet"]

# Caddyfile heredoc markers may only contain alphanumerics, dashes, and
# underscores (mirrors Caddy's caddyfile/lexer.go ``heredocMarkerRegexp``).
_HEREDOC_MARKER_RE = re.compile(r"[A-Za-z0-9_-]+")


def _finalize_heredoc(val: str, marker: str) -> str | None:
    r"""Reproduce Caddy's ``lexer.finalizeHeredoc`` body stripping.

    ``val`` is the full heredoc payload *including* the trailing marker, exactly
    as Caddy's lexer accumulates it before finalizing. The leading padding to
    strip is whatever precedes the marker on its line (Caddy does NOT require it
    to be whitespace); each content line must start with that padding. Returns
    the resulting token ``Text``, or ``None`` when a line's prefix does not match
    the padding — in which case Caddy raises and the config never loads, so the
    token yields no block delimiter.
    """
    last_nl = val.rfind("\n")
    lines = val[: last_nl + 1].split("\n")
    padding = val[last_nl + 1 : len(val) - len(marker)]
    out: list[str] = []
    for line in lines[:-1]:
        if line == "" or line == "\r":
            out.append("\n")
            continue
        if not line.startswith(padding):
            return None
        out.append((line[len(padding):] + "\n").replace("\r", ""))
    text = "".join(out)
    if text.endswith("\n"):
        text = text[:-1]
    return text


def validate_caddy_snippet(v: str) -> str:
    r"""Reject a custom Caddy snippet that would break OUT of its site block.

    The snippet is injected inside a per-service site block
    ``host { ... <snippet> ... }``. We deliberately do NOT sanitize Caddy
    directives — arbitrary in-block directives (matchers, ``handle { ... }``
    blocks, ``header_up Host {host}`` placeholders) are a supported feature.
    The only containment requirement is brace balance: a stray ``}`` would
    close (escape) the surrounding site block, and a dangling ``{`` would
    swallow the block's own closing brace.

    We validate the *rendered* form (see ``render_snippet_block``), i.e. the
    exact bytes Caddy will tokenize, because the renderer's strip + per-line
    line-break normalisation can change the token stream.

    A ``{``/``}`` *character* count is not enough, and "ignore every brace that
    is quoted or in a heredoc" is WRONG. Caddy's parser decides block delimiters
    purely by a token's final **Text**: a token opens/closes a block iff its text
    is exactly ``{`` or ``}`` — *regardless of how it was quoted*. Consequences:

    - A brace glued to other text (``backend{``, ``{host}``, ``ok}``) is ordinary
      token content, never a delimiter.
    - A standalone ``{`` / ``}`` token (whitespace-separated) IS a delimiter.
    - A *whole-token* quoted/backtick brace — ``"}"`` or `` `}` `` — lexes to
      Text ``}`` and IS a delimiter too: it can close (escape) the site block.
      (Only braces that are *part of* a longer string — ``"a}"`` — stay inert.)
    - A heredoc whose stripped body is exactly ``}`` (e.g. ``<<X``, ``}``, ``X``
      on three lines) also lexes to Text ``}`` and closes a block.
    - Braces hidden in a line comment (``# }``), or behind a backslash escape
      (``\}``), are inert. Caddy keeps a pending backslash across blank space
      with no token text yet, so ``\ }`` escapes the brace into the literal token
      ``\}`` (NOT a ``}`` delimiter) — only ``\`` before a newline is dropped.

    So we tokenize the way Caddy's lexer does and apply these checks:

    1. Balance the block-**delimiter** tokens: a token whose full text is ``{``
       (open) or ``}`` (close), whether bare, double-quoted, or backtick-quoted.
       Depth must never go negative (escape via ``}``) and must end at zero (a
       dangling ``{`` would swallow the site block's own closing brace).
    2. A heredoc token's delimiter status is keyed off its *finalized body*,
       computed exactly like Caddy's ``lexer.finalizeHeredoc`` (per-line padding
       stripping — the padding is whatever precedes the closing marker and is NOT
       required to be whitespace). A body that finalizes to ``}`` is a close (it
       can escape) and one that finalizes to ``{`` is an open (a dangling ``{``
       swallows the site block's own closing brace). Plain ``str.strip`` is WRONG
       here: it misses non-whitespace padding (``<<M`` / ``z}`` / ``zM`` → text
       ``}``) and never models the ``{`` opener.
    3. Any *other* unquoted token that carries active braces must be internally
       balanced (``{host}`` is fine; ``{host``, ``backend{`` and ``b}`` are
       rejected). This kills the glued-brace smuggling vector and catches
       malformed placeholders.
    4. An unterminated quoted string or heredoc at EOF is rejected: in the
       rendered Caddyfile it never closes before the site block does, so it
       consumes the block's own closing brace (a dangling-open variant).
    """
    s = render_snippet_block(v)
    depth = 0               # standalone-delimiter nesting (check 1)
    tok: list[str] = []     # reconstructed text of the in-progress token
    tdepth = 0              # brace depth of active chars within this token (check 2)
    tmin = 0                # lowest tdepth reached within this token
    comment = quoted = btquoted = in_heredoc = heredoc_escaped = escaped = False
    marker = ""

    def end_token() -> None:
        nonlocal depth, tdepth, tmin, quoted, btquoted, escaped, heredoc_escaped
        text = "".join(tok)
        if text == "{":
            depth += 1
        elif text == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    "Custom Caddy snippet has an unbalanced '}' that would "
                    "escape its site block"
                )
        elif tmin < 0 or tdepth != 0:
            raise ValueError(
                "Custom Caddy snippet has an unbalanced brace in a directive "
                "token that could escape its site block"
            )
        tok.clear()
        tdepth = 0
        tmin = 0
        quoted = btquoted = escaped = heredoc_escaped = False

    i, n = 0, len(s)
    while i < n:
        ch = s[i]
        i += 1

        # Heredoc opening marker: triggered once the current token starts "<<".
        if (
            not quoted
            and not btquoted
            and not in_heredoc
            and not heredoc_escaped
            and len(tok) >= 2
            and tok[0] == "<"
            and tok[1] == "<"
        ):
            if ch == " ":
                end_token()
                continue
            if ch == "\r":
                continue
            if ch == "\n":
                candidate = "".join(tok[2:])
                if candidate and _HEREDOC_MARKER_RE.fullmatch(candidate):
                    in_heredoc = True
                    marker = candidate
                    tok.clear()
                    tdepth = tmin = 0
                else:
                    # Invalid/empty marker: Caddy errors out; the partial token
                    # holds no active brace, so just drop it.
                    end_token()
                continue
            tok.append(ch)
            continue

        # A heredoc body is literal text, so a brace *within* it is not a
        # token-internal delimiter. BUT Caddy finalizes the body and keys block
        # delimiters off the resulting token Text: a body that finalizes to ``}``
        # CLOSES a block (escape) and one that finalizes to ``{`` OPENS one (a
        # dangling open swallows the site block's own closing brace). We finalize
        # exactly like Caddy and apply the same delimiter rule as a bare token.
        if in_heredoc:
            tok.append(ch)
            if len(tok) >= len(marker) and "".join(tok[-len(marker):]) == marker:
                text = _finalize_heredoc("".join(tok), marker)
                if text == "}":
                    depth -= 1
                    if depth < 0:
                        raise ValueError(
                            "Custom Caddy snippet has an unbalanced '}' (a "
                            "heredoc body) that would escape its site block"
                        )
                elif text == "{":
                    depth += 1
                in_heredoc = False
                marker = ""
                tok.clear()
                tdepth = tmin = 0
            continue

        # Backslash escapes the next char (outside backtick strings); an escaped
        # ``{``/``}`` is literal text, not a delimiter.
        if not escaped and not btquoted and ch == "\\":
            escaped = True
            continue

        # Quoted / backtick strings: a brace *inside* a longer string is literal
        # data (no token-internal delimiter — tdepth/tmin untouched). But Caddy
        # keys block delimiters off the token Text regardless of quoting, so a
        # whole-token ``"{"`` / ``"}"`` (or backtick) still opens/closes a block.
        # We therefore collect the literal content and let end_token() apply the
        # same ``text == "{"/"}"`` delimiter rule it uses for bare tokens.
        if quoted or btquoted:
            if quoted and escaped:
                # Caddy keeps the backslash for every escape except an escaped
                # quote, so an escaped brace (``"\}"``) has text ``\}`` (not ``}``).
                if ch != '"':
                    tok.append("\\")
                tok.append(ch)
                escaped = False
                continue
            if (quoted and ch == '"') or (btquoted and ch == "`"):
                end_token()
                continue
            tok.append(ch)
            continue

        # Whitespace ends the current token.
        if ch.isspace():
            if ch == "\r":
                continue
            if ch == "\n":
                # A backslash before a newline escapes it (line continuation):
                # Caddy drops the pending escape instead of escaping a token char.
                if escaped:
                    escaped = False
                comment = False
                if tok:
                    end_token()
                continue
            # Any OTHER whitespace (space, tab, ...) must NOT clear a pending
            # escape while no token text has accumulated: Caddy carries ``escaped``
            # across blanks, so a leading ``\`` escapes the next non-blank char
            # (``\ }`` → literal token ``\}``, never a ``}`` delimiter). Clearing
            # it here would miscount the following brace and miss the escape.
            if tok:
                end_token()
            continue

        # A '#' begins a line comment only at the start of a token.
        if ch == "#" and not tok:
            comment = True
        if comment:
            continue

        # Quote / backtick strings open only at the start of a token.
        if not tok:
            if ch == '"':
                quoted = True
                continue
            if ch == "`":
                btquoted = True
                continue

        if escaped:
            if ch == "<":
                heredoc_escaped = True
            else:
                tok.append("\\")
            escaped = False
            tok.append(ch)
            continue

        # Active (unquoted, unescaped) config syntax: real braces live here.
        if ch == "{":
            tdepth += 1
        elif ch == "}":
            tdepth -= 1
            if tdepth < tmin:
                tmin = tdepth
        tok.append(ch)

    # An unterminated quoted string or heredoc never closes before the rendered
    # site block does, so it swallows the block's own closing brace (a
    # dangling-open escape). Reject it rather than dropping the partial token.
    if quoted or btquoted or in_heredoc:
        raise ValueError(
            "Custom Caddy snippet has an unterminated quoted string or heredoc "
            "that would consume its site block's closing brace"
        )
    if tok or escaped:
        end_token()

    if depth != 0:
        raise ValueError(
            "Custom Caddy snippet has an unbalanced '{' that would escape "
            "its site block"
        )
    return v
