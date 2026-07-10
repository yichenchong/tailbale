"""Caddy snippet-containment lexer (block-escape guard).

Load-bearing security code: this validator lexes the *rendered* form of a
user-supplied custom Caddy snippet exactly the way Caddy's caddyfile lexer
does, to guarantee the snippet cannot break OUT of its per-service site block.
It lives beside ``render_snippet_block`` (the renderer it guards) so the two
share a single source of truth: the validated bytes can never diverge from
what is rendered into the Caddyfile.

The lexer logic was converged over prior fuzz rounds and MUST NOT change. It is
factored into ``_CaddySnippetLexer`` — one named method per token rule
(heredoc marker/body, escape, quote/backtick, whitespace, comment, delimiter,
EOF finalize) — purely so each rule is reviewable in isolation; the token
stream and every decision are byte-for-byte identical to the prior inline loop.
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


class _CaddySnippetLexer:
    r"""Lex a rendered snippet like Caddy's caddyfile lexer and check containment.

    State mirrors the prior inline loop exactly; ``feed`` dispatches each char
    through the ordered token rules and ``finish`` applies the EOF checks. The
    balance checks are:

    1. Balance the block-**delimiter** tokens: a token whose full text is ``{``
       (open) or ``}`` (close), whether bare, double-quoted, or backtick-quoted.
       Depth must never go negative (escape via ``}``) and must end at zero (a
       dangling ``{`` would swallow the site block's own closing brace).
    2. A heredoc token's delimiter status is keyed off its *finalized body*,
       computed exactly like Caddy's ``lexer.finalizeHeredoc``. A body that
       finalizes to ``}`` is a close (it can escape) and one that finalizes to
       ``{`` is an open.
    3. Any *other* unquoted token that carries active braces must be internally
       balanced (``{host}`` is fine; ``{host``, ``backend{`` and ``b}`` are
       rejected). This kills the glued-brace smuggling vector.
    4. An unterminated quoted string or heredoc at EOF is rejected: in the
       rendered Caddyfile it never closes before the site block does.
    """

    def __init__(self, s: str) -> None:
        self.s = s
        self.depth = 0               # standalone-delimiter nesting (check 1)
        self.tok: list[str] = []     # reconstructed text of the in-progress token
        self.tdepth = 0              # brace depth of active chars in token (check 2)
        self.tmin = 0                # lowest tdepth reached within this token
        self.comment = False
        self.quoted = False
        self.btquoted = False
        self.in_heredoc = False
        self.heredoc_escaped = False
        self.escaped = False
        self.marker = ""

    def run(self) -> None:
        """Lex the whole rendered string, raising ``ValueError`` on any escape."""
        for ch in self.s:
            self.feed(ch)
        self.finish()

    def feed(self, ch: str) -> None:
        """Route one char through the ordered token rules (first handler wins)."""
        if self._heredoc_marker(ch):
            return
        if self._heredoc_body(ch):
            return
        if self._escape_start(ch):
            return
        if self._quoted(ch):
            return
        if self._whitespace(ch):
            return
        if self._comment(ch):
            return
        if self._quote_open(ch):
            return
        if self._escaped_char(ch):
            return
        self._delimiter_char(ch)

    def _end_token(self) -> None:
        """Close the current token and apply the block-delimiter balance rule."""
        text = "".join(self.tok)
        if text == "{":
            self.depth += 1
        elif text == "}":
            self.depth -= 1
            if self.depth < 0:
                raise ValueError(
                    "Custom Caddy snippet has an unbalanced '}' that would "
                    "escape its site block"
                )
        elif self.tmin < 0 or self.tdepth != 0:
            raise ValueError(
                "Custom Caddy snippet has an unbalanced brace in a directive "
                "token that could escape its site block"
            )
        self.tok.clear()
        self.tdepth = 0
        self.tmin = 0
        self.quoted = self.btquoted = self.escaped = self.heredoc_escaped = False

    def _heredoc_marker(self, ch: str) -> bool:
        """Consume the marker line once the current token starts ``<<``."""
        if not (
            not self.quoted
            and not self.btquoted
            and not self.in_heredoc
            and not self.heredoc_escaped
            and len(self.tok) >= 2
            and self.tok[0] == "<"
            and self.tok[1] == "<"
        ):
            return False
        if ch == " ":
            self._end_token()
            return True
        if ch == "\r":
            return True
        if ch == "\n":
            candidate = "".join(self.tok[2:])
            if candidate and _HEREDOC_MARKER_RE.fullmatch(candidate):
                self.in_heredoc = True
                self.marker = candidate
                self.tok.clear()
                self.tdepth = self.tmin = 0
            else:
                # Invalid/empty marker: Caddy errors out; the partial token
                # holds no active brace, so just drop it.
                self._end_token()
            return True
        self.tok.append(ch)
        return True

    def _heredoc_body(self, ch: str) -> bool:
        r"""Accumulate a heredoc body and, on the marker, key delimiters off its
        finalized Text: a body that finalizes to ``}`` CLOSES a block (escape)
        and one that finalizes to ``{`` OPENS one. We finalize exactly like
        Caddy and apply the same delimiter rule as a bare token."""
        if not self.in_heredoc:
            return False
        self.tok.append(ch)
        if (
            len(self.tok) >= len(self.marker)
            and "".join(self.tok[-len(self.marker):]) == self.marker
        ):
            text = _finalize_heredoc("".join(self.tok), self.marker)
            if text == "}":
                self.depth -= 1
                if self.depth < 0:
                    raise ValueError(
                        "Custom Caddy snippet has an unbalanced '}' (a "
                        "heredoc body) that would escape its site block"
                    )
            elif text == "{":
                self.depth += 1
            self.in_heredoc = False
            self.marker = ""
            self.tok.clear()
            self.tdepth = self.tmin = 0
        return True

    def _escape_start(self, ch: str) -> bool:
        """Begin a backslash escape (outside backtick strings)."""
        if not self.escaped and not self.btquoted and ch == "\\":
            self.escaped = True
            return True
        return False

    def _quoted(self, ch: str) -> bool:
        r"""Collect quoted/backtick content as literal data. A brace *inside* a
        longer string stays inert (tdepth/tmin untouched); a whole-token ``"{"``
        / `` `}` `` still opens/closes via the ``_end_token`` delimiter rule."""
        if not (self.quoted or self.btquoted):
            return False
        if self.quoted and self.escaped:
            # Caddy keeps the backslash for every escape except an escaped
            # quote, so an escaped brace (``"\}"``) has text ``\}`` (not ``}``).
            if ch != '"':
                self.tok.append("\\")
            self.tok.append(ch)
            self.escaped = False
            return True
        if (self.quoted and ch == '"') or (self.btquoted and ch == "`"):
            self._end_token()
            return True
        self.tok.append(ch)
        return True

    def _whitespace(self, ch: str) -> bool:
        """End the current token on whitespace (with line-continuation rules)."""
        if not ch.isspace():
            return False
        if ch == "\r":
            return True
        if ch == "\n":
            # A backslash before a newline escapes it (line continuation):
            # Caddy drops the pending escape instead of escaping a token char.
            if self.escaped:
                self.escaped = False
            self.comment = False
            if self.tok:
                self._end_token()
            return True
        # Any OTHER whitespace (space, tab, ...) must NOT clear a pending
        # escape while no token text has accumulated: Caddy carries ``escaped``
        # across blanks, so a leading ``\`` escapes the next non-blank char
        # (``\ }`` -> literal token ``\}``, never a ``}`` delimiter). Clearing
        # it here would miscount the following brace and miss the escape.
        if self.tok:
            self._end_token()
        return True

    def _comment(self, ch: str) -> bool:
        """A ``#`` begins a line comment only at the start of a token."""
        if ch == "#" and not self.tok:
            self.comment = True
        return self.comment

    def _quote_open(self, ch: str) -> bool:
        """Quote / backtick strings open only at the start of a token."""
        if not self.tok:
            if ch == '"':
                self.quoted = True
                return True
            if ch == "`":
                self.btquoted = True
                return True
        return False

    def _escaped_char(self, ch: str) -> bool:
        """Apply a pending backslash escape to a bare-token char."""
        if self.escaped:
            if ch == "<":
                self.heredoc_escaped = True
            else:
                self.tok.append("\\")
            self.escaped = False
            self.tok.append(ch)
            return True
        return False

    def _delimiter_char(self, ch: str) -> None:
        """Active (unquoted, unescaped) config syntax: real braces live here."""
        if ch == "{":
            self.tdepth += 1
        elif ch == "}":
            self.tdepth -= 1
            if self.tdepth < self.tmin:
                self.tmin = self.tdepth
        self.tok.append(ch)

    def finish(self) -> None:
        """Apply the EOF checks: reject dangling strings/heredocs and imbalance."""
        # An unterminated quoted string or heredoc never closes before the
        # rendered site block does, so it swallows the block's own closing brace
        # (a dangling-open escape). Reject it rather than dropping the token.
        if self.quoted or self.btquoted or self.in_heredoc:
            raise ValueError(
                "Custom Caddy snippet has an unterminated quoted string or heredoc "
                "that would consume its site block's closing brace"
            )
        if self.tok or self.escaped:
            self._end_token()

        if self.depth != 0:
            raise ValueError(
                "Custom Caddy snippet has an unbalanced '{' that would escape "
                "its site block"
            )


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
    _CaddySnippetLexer(render_snippet_block(v)).run()
    return v
