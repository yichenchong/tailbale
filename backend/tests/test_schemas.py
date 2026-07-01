"""Tests for Pydantic request/response schemas.

Covers the consolidated hostname validator, port/name validators, ServiceUpdate
null-rejection, base-domain normalization, and a guard that no *Response model
serializes a secret (token / auth key / api key / password) back to a client.
"""

import inspect

import pytest
from pydantic import BaseModel, ValidationError

from app.schemas import auth as auth_schemas
from app.schemas import services as service_schemas
from app.schemas import settings as settings_schemas
from app.schemas.services import (
    ServiceCreate,
    ServiceResponse,
    ServiceUpdate,
    _validate_hostname,
)
from app.schemas.settings import GeneralSettingsUpdate

_CREATE_BASE = dict(
    name="App",
    upstream_container_id="abc123",
    upstream_container_name="app",
    upstream_port=80,
    hostname="app.example.com",
)


def _make_create(**overrides):
    data = dict(_CREATE_BASE)
    data.update(overrides)
    return ServiceCreate(**data)


# ---------------------------------------------------------------------------
# Consolidated hostname validator
# ---------------------------------------------------------------------------


class TestHostnameValidator:
    @pytest.mark.parametrize(
        "hostname",
        ["a.example.com", "x.y.z.example.com", "a" * 63 + ".example.com", "localhost"],
    )
    def test_valid_hostnames_accepted(self, hostname):
        assert _validate_hostname(hostname) == hostname

    @pytest.mark.parametrize(
        "hostname",
        [
            "INVALID HOSTNAME!",
            "Upper.Example.com",  # uppercase rejected (used verbatim)
            "-leadinghyphen.com",
            "trailinghyphen-.com",
            "double..dot.com",
            "under_score.com",
        ],
    )
    def test_malformed_hostnames_rejected(self, hostname):
        with pytest.raises(ValueError, match="Invalid hostname format"):
            _validate_hostname(hostname)

    def test_trailing_newline_rejected(self):
        # Regression: re.match() anchors `$` before a final "\n", so
        # "ok.example.com\n" sneaked past the old validator. The hostname is
        # used verbatim as the on-disk cert dir name and inside the Caddyfile,
        # so an embedded control char is a genuine injection footgun.
        with pytest.raises(ValueError, match="Invalid hostname format"):
            _validate_hostname("ok.example.com\n")

    @pytest.mark.parametrize("hostname", ["\nok.com", "ok.com\nx", "ok.com\t"])
    def test_embedded_control_chars_rejected(self, hostname):
        with pytest.raises(ValueError, match="Invalid hostname format"):
            _validate_hostname(hostname)

    def test_total_length_253_accepted_254_rejected(self):
        def mk(total):
            parts, rem = [], total
            while rem > 0:
                take = min(63, rem)
                parts.append("a" * take)
                rem -= take
                if rem > 0:
                    rem -= 1  # account for the joining dot
            return ".".join(parts)

        h253 = mk(253)
        assert len(h253) == 253
        assert _validate_hostname(h253) == h253

        h254 = mk(254)
        assert len(h254) == 254
        with pytest.raises(ValueError, match="must not exceed 253"):
            _validate_hostname(h254)

    def test_label_over_63_rejected(self):
        with pytest.raises(ValueError, match="label must not exceed 63"):
            _validate_hostname("a" * 64 + ".example.com")

    def test_create_rejects_newline_hostname(self):
        with pytest.raises(ValidationError):
            _make_create(hostname="app.example.com\n")

    def test_update_hostname_optional_omitted_ok(self):
        # Omitting hostname must not trip the validator (it stays None).
        m = ServiceUpdate(name="NewName")
        assert m.hostname is None

    def test_update_rejects_newline_hostname(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(hostname="app.example.com\n")


# ---------------------------------------------------------------------------
# Port + name validators
# ---------------------------------------------------------------------------


class TestPortAndNameValidators:
    @pytest.mark.parametrize("port", [1, 80, 65535])
    def test_valid_ports(self, port):
        assert _make_create(upstream_port=port).upstream_port == port

    @pytest.mark.parametrize("port", [0, -1, 65536, 70000])
    def test_out_of_range_ports_rejected(self, port):
        with pytest.raises(ValidationError):
            _make_create(upstream_port=port)

    def test_name_is_stripped(self):
        assert _make_create(name="  Hi  ").name == "Hi"

    @pytest.mark.parametrize("name", ["", "   ", "\t\n"])
    def test_blank_name_rejected(self, name):
        with pytest.raises(ValidationError):
            _make_create(name=name)

    def test_scheme_pattern_enforced(self):
        assert _make_create(upstream_scheme="https").upstream_scheme == "https"
        with pytest.raises(ValidationError):
            _make_create(upstream_scheme="ftp")


# ---------------------------------------------------------------------------
# ServiceUpdate null-rejection for non-nullable fields
# ---------------------------------------------------------------------------


class TestServiceUpdateNullRejection:
    @pytest.mark.parametrize(
        "field",
        ["name", "upstream_scheme", "upstream_port", "hostname", "enabled", "preserve_host_header"],
    )
    def test_explicit_null_rejected(self, field):
        with pytest.raises(ValidationError):
            ServiceUpdate(**{field: None})

    @pytest.mark.parametrize("field", ["healthcheck_path", "custom_caddy_snippet", "app_profile"])
    def test_nullable_fields_accept_null(self, field):
        # These map to nullable columns — explicit null clears them, must pass.
        m = ServiceUpdate(**{field: None})
        assert getattr(m, field) is None


# ---------------------------------------------------------------------------
# base_domain normalization
# ---------------------------------------------------------------------------


class TestBaseDomainNormalization:
    def test_lowercased(self):
        assert GeneralSettingsUpdate(base_domain="Example.COM").base_domain == "example.com"

    def test_whitespace_stripped(self):
        assert GeneralSettingsUpdate(base_domain="  example.com  ").base_domain == "example.com"

    def test_invalid_rejected(self):
        with pytest.raises(ValidationError):
            GeneralSettingsUpdate(base_domain="not a domain!")


class TestGeneralSettingsIntValidation:
    _INT_FIELDS = (
        "reconcile_interval_seconds",
        "health_check_interval_seconds",
        "cert_renewal_window_days",
        "event_retention_days",
    )

    @pytest.mark.parametrize("field", _INT_FIELDS)
    @pytest.mark.parametrize("bad", [0, -1, 1.5, "", "   ", "abc"])
    def test_rejects_invalid_ints(self, field, bad):
        # ge=1 + int typing reject zero/negative/fractional/blank/garbage so a
        # bad cadence or retention window can never be written.
        with pytest.raises(ValidationError):
            GeneralSettingsUpdate(**{field: bad})

    @pytest.mark.parametrize("field", _INT_FIELDS)
    def test_accepts_positive_int(self, field):
        assert getattr(GeneralSettingsUpdate(**{field: 5}), field) == 5

    @pytest.mark.parametrize("field", _INT_FIELDS)
    def test_coerces_clean_numeric_string(self, field):
        assert getattr(GeneralSettingsUpdate(**{field: "42"}), field) == 42


# ---------------------------------------------------------------------------
# Response models must never leak secrets
# ---------------------------------------------------------------------------


def _response_models():
    for mod in (service_schemas, settings_schemas, auth_schemas):
        for name, obj in inspect.getmembers(mod, inspect.isclass):
            if (
                issubclass(obj, BaseModel)
                and obj.__module__ == mod.__name__
                and name.endswith("Response")
            ):
                yield name, obj


class TestResponseSecretLeakage:
    _SECRET_TOKENS = (
        "token", "auth_key", "authkey", "api_key", "apikey",
        "password", "passwd", "secret", "hash", "private",
    )

    def test_no_response_model_exposes_a_secret_field(self):
        offenders = []
        for name, model in _response_models():
            for field in model.model_fields:
                low = field.lower()
                # `*_configured` booleans only report presence, never the value.
                if low.endswith("_configured"):
                    continue
                if any(tok in low for tok in self._SECRET_TOKENS):
                    offenders.append(f"{name}.{field}")
        assert not offenders, f"Response models leak secret-like fields: {offenders}"

    def test_user_response_has_no_password(self):
        fields = set(auth_schemas.UserResponse.model_fields)
        assert "password" not in fields
        assert "password_hash" not in fields

    def test_secret_settings_reported_as_booleans_only(self):
        cf = settings_schemas.CloudflareSettingsResponse.model_fields
        ts = settings_schemas.TailscaleSettingsResponse.model_fields
        assert "token" not in cf and "token_configured" in cf
        assert "auth_key" not in ts and "auth_key_configured" in ts
        assert "api_key" not in ts and "api_key_configured" in ts


# ---------------------------------------------------------------------------
# upstream_container_name charset validator (Caddyfile block-injection guard)
# ---------------------------------------------------------------------------


class TestUpstreamContainerNameValidator:
    @pytest.mark.parametrize(
        "name",
        ["nextcloud", "c123", "my-app_1.2", "app", "A", "0", "x.y_z-1"],
    )
    def test_valid_names_accepted(self, name):
        # Every legitimate Docker name (and all existing fixtures) must pass.
        assert _make_create(upstream_container_name=name).upstream_container_name == name

    @pytest.mark.parametrize(
        "name",
        [
            "my app",                # whitespace
            "app\nevil",             # embedded newline
            "app\n",                 # trailing newline (re.match footgun)
            "app;rm -rf",            # semicolon
            "app}\nexample.com {",   # brace-escape attempt
            "app{x}",                # braces
            'app"x',                 # double quote
            "app'x",                 # single quote
            "_leading",              # underscore start (Docker requires alnum)
            "-leading",              # hyphen start
            ".leading",              # dot start
        ],
    )
    def test_injection_names_rejected(self, name):
        with pytest.raises(ValidationError):
            _make_create(upstream_container_name=name)


# ---------------------------------------------------------------------------
# custom_caddy_snippet brace-containment validator (block-escape guard)
# ---------------------------------------------------------------------------


class TestCaddySnippetContainment:
    @pytest.mark.parametrize(
        "snippet",
        [
            "header_up Host {host}",                          # balanced placeholder
            "handle /api/* {\n\treverse_proxy api:80\n}",     # balanced handle block
            "encode gzip",                                    # no braces at all
            "@m path /a {\n}\nhandle @m {\n}",                # multiple balanced blocks
            "",                                               # empty snippet
            # The braces below are inert in Caddy (comment / quoted string), so a
            # raw character counter wrongly rejected these legitimate snippets.
            "reverse_proxy api:80  # enable { and } later",   # braces in a comment
            'respond "stray brace: }"',                       # unbalanced brace in a string
            'respond `{"json": "body}"}`',                    # braces in a backtick string
            "header_up X {http.request.host}{remote_host}",   # adjacent placeholders
            "respond <<HTML\n<h1>hi {x}</h1>\nHTML",          # heredoc body is literal
        ],
    )
    def test_balanced_snippets_accepted_create(self, snippet):
        # Arbitrary in-block directives stay allowed; only block-escape is blocked.
        assert _make_create(custom_caddy_snippet=snippet).custom_caddy_snippet == snippet

    @pytest.mark.parametrize(
        "snippet",
        [
            "}\nexample.com {\n\treverse_proxy evil:80\n}",   # closes site block early
            "}",                                              # bare escape
            "} redirect /admin",                              # leading close then directive
            "handle {",                                       # dangling open
            "header_up Host {host",                           # unbalanced open brace
            "backend{\n}\nexample.com {\n\treverse_proxy x",  # glued '{' masks a real '}'
        ],
    )
    def test_block_escaping_snippets_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_balanced_snippet_accepted_update(self):
        m = ServiceUpdate(custom_caddy_snippet="handle { reverse_proxy api:80 }")
        assert m.custom_caddy_snippet == "handle { reverse_proxy api:80 }"

    def test_block_escaping_snippet_rejected_update(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet="}\nexample.com {\n}")

    def test_update_snippet_null_still_accepted(self):
        # Nullable field: None clears it and must bypass the brace scan.
        assert ServiceUpdate(custom_caddy_snippet=None).custom_caddy_snippet is None

    # ------------------------------------------------------------------
    # Lexer-aware containment: a raw '{'/'}' character count is bypassable.
    # Caddy only treats a *standalone* brace token as a block delimiter and
    # ignores braces inside comments, quoted strings, and heredoc bodies. Each
    # snippet below balances to zero at the character level (so the old counter
    # accepted it) yet contains a real, standalone '}' that escapes the site
    # block in Caddy. They must all be rejected.
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "snippet",
        [
            # A '{' hidden in a comment offsets the counter so the real '}'
            # on the next line looks balanced.
            "# {\n}\nexample.com {\n\treverse_proxy evil:80\n# }",
            # A '{' glued to a directive arg ('a{') is not a Caddy delimiter,
            # but the bare '}' after it closes the site block. 'b}' rebalances
            # the naive counter while staying a valid directive arg.
            "respond a{\n}\nexample.com {\n\trespond b}",
            # An empty heredoc body (marker on the very next line) ends the
            # heredoc immediately; the following bare '}' then escapes.
            "respond <<E\nE\n}\nexample.com {\n\treverse_proxy evil:80",
            # A '{' that is only PART of a heredoc body ('{x' -> Text '{x') is
            # genuinely inert, so the following bare '}' escapes. (A body of
            # exactly '{' is NOT inert -- it finalizes to Text '{' and OPENS a
            # block; that opener case is covered as an accepted/contained config.)
            "respond <<HD\n{x\nHD\n}\nexample.com {\n\trespond x <<H2\n}\nH2",
            # Quoted '{' (+1) and the standalone '}' (-1) net out, but
            # 'example.com {' is then left dangling open -> depth != 0 -> reject.
            '"{"\n}\nexample.com {\n\treverse_proxy evil:80',
        ],
    )
    def test_lexer_brace_bypass_vectors_rejected(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_comment_hidden_brace_bypass_rejected_update(self):
        # Same comment-hiding bypass, exercised through ServiceUpdate.
        with pytest.raises(ValidationError):
            ServiceUpdate(
                custom_caddy_snippet="# {\n}\nexample.com {\n\trespond evil\n# }"
            )

    def test_inert_braces_in_comment_and_string_accepted(self):
        # Braces that live only in a comment or a quoted string are not
        # delimiters and must not trip the balance check (the old character
        # counter wrongly rejected both of these).
        assert _make_create(
            custom_caddy_snippet="reverse_proxy api:80  # closes with }"
        ).custom_caddy_snippet is not None
        assert _make_create(
            custom_caddy_snippet='respond "unterminated brace {"'
        ).custom_caddy_snippet is not None

    # ------------------------------------------------------------------
    # Quoted / backtick / heredoc whole-token brace escapes (regression).
    # Caddy's parser keys block delimiters off a token's final *Text*,
    # ignoring how it was quoted: a standalone "}" / `}` (double- or
    # backtick-quoted), or a heredoc whose stripped body is exactly "}",
    # all lex to Text "}" and CLOSE (escape) the per-service site block.
    # The previous lexer treated every quoted/heredoc brace as inert and so
    # ACCEPTED all of these (a genuine block-escape bypass). They must be
    # rejected.
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "snippet",
        [
            '"}"',                                            # quoted close: bare escape
            "`}`",                                            # backtick close: bare escape
            'respond "}"',                                    # quoted close as a directive arg
            "respond `}`",                                    # backtick close as a directive arg
            # Clean two-site injection: the quoted "}" closes the site block, a
            # fully attacker-controlled site is defined, and a trailing quoted
            # "{" absorbs the site block's real closing brace (no parse garbage).
            '"}"\nevil.com {\n\treverse_proxy attacker:80\n}\nfoo.com "{"',
            "`}`\nevil.com {\n\treverse_proxy attacker:80\n}\nfoo.com `{`",
            # Heredoc whose stripped body is exactly "}" -> Text "}" -> a closer.
            "<<X\n}\nX",
            "respond <<X\n}\nX",
            "<<X\n}\nX\nevil.com {\n\treverse_proxy attacker:80\n}",
            # Body indentation is stripped relative to the closing marker, so an
            # indented body still yields Text "}".
            "respond <<X\n  }\n  X",
        ],
    )
    def test_quoted_and_heredoc_brace_escape_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_quoted_close_escape_rejected_update(self):
        # Same quoted-"}" escape, exercised through ServiceUpdate.
        with pytest.raises(ValidationError):
            ServiceUpdate(
                custom_caddy_snippet='"}"\nevil.com {\n\treverse_proxy x\n}\nz "{"'
            )

    def test_heredoc_close_escape_rejected_update(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet="<<X\n}\nX")

    def test_quoted_braces_count_as_balanced_delimiters(self):
        # Quoted "{"/"}" are real delimiters (matching Caddy), so a balanced
        # pair is accepted while either one alone escapes / dangles.
        assert _make_create(
            custom_caddy_snippet='"{"\n\trespond hi\n"}"'
        ).custom_caddy_snippet is not None
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet='"{"')   # dangling open
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet='"}"')   # escape

    @pytest.mark.parametrize(
        "snippet",
        [
            'respond "x}"',                  # brace is part of a longer string -> inert
            "respond `x}`",                  # ditto, backtick
            r'"\}"',                          # escaped brace in a string -> text "\}", inert
            r'"\"}"',                         # escaped quote does not terminate the string
            "respond <<X\n}foo\nX",          # body "}foo" is not exactly "}" -> inert
            'respond "a {nested} brace"',    # balanced braces inside a string -> inert
        ],
    )
    def test_braces_inside_strings_and_heredocs_still_accepted(self, snippet):
        # A brace that is only PART of a quoted string / heredoc body (not the
        # whole token Text) is literal data in Caddy and must stay accepted.
        assert _make_create(custom_caddy_snippet=snippet).custom_caddy_snippet == snippet

    # ------------------------------------------------------------------
    # Heredoc finalized-body brace escapes (round-4 regression).
    # Caddy's lexer.finalizeHeredoc strips the padding that precedes the closing
    # marker from every body line, and that padding is NOT required to be
    # whitespace. A body that finalizes to "}" / "{" therefore lexes to Text
    # "}" / "{" and CLOSES / OPENS a block, escaping the per-service site block.
    # The previous lexer used str.strip (which misses non-whitespace padding) and
    # left every heredoc "{" inert (never modelling the opener), so it ACCEPTED
    # all of these escapes. They must be rejected.
    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "snippet",
        [
            # non-whitespace padding 'z' is stripped -> Text '}' -> closer (escape)
            "<<M\nz}\nzM",
            "respond <<X\nz}\nzX",
            "respond <<EOF\nab}\nabEOF",
            # padding 'z ' (non-ws + trailing space) is still stripped to '}'
            "respond <<X\nz }\nz X",
            # CRLF body with non-ws padding -> Text '}' -> closer
            "<<M\r\nz}\r\nzM",
            # body that finalizes to '{' is an OPENER that dangles the site block
            # open (the pre-fix lexer left every heredoc '{' inert)
            "respond <<X\n{\nX",
            "respond <<X\n  {\n  X",
            "<<M\nz{\nzM",
            # full two-site injection: a heredoc '}' closes the site block, an
            # attacker site is defined, then a heredoc '{' reopens to swallow the
            # site block's real closing brace -- all via heredocs the old lexer
            # treated as inert.
            "<<M\nz}\nzM\nevil.com {\n\treverse_proxy attacker:80\n}\n<<X\n  {\n  X",
        ],
    )
    def test_heredoc_finalized_brace_escape_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_heredoc_nonws_padding_close_escape_rejected_update(self):
        # Non-whitespace-padded heredoc '}' escape, exercised through ServiceUpdate.
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet="<<M\nz}\nzM")

    def test_heredoc_open_dangle_rejected_update(self):
        # Heredoc-body '{' opener dangles the site block open.
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet="respond <<X\n{\nX")

    @pytest.mark.parametrize(
        "snippet",
        [
            # A heredoc '{' opener balanced by a matching close stays CONTAINED in
            # Caddy and must be accepted. The pre-fix lexer left the heredoc '{'
            # inert and then saw the lone closing '}' as an escape -> wrong reject.
            "<<X\n{\nX\n}",
            "<<X\n  {\n  X\n}",
            "<<M\nz{\nzM\n}",
            '"{"\n<<M\nz}\nzM',
            # heredoc '{' opener closed by another heredoc '}' (both finalized).
            "<<M\nz{\nzM\n<<X\nz}\nzX",
            # a body that finalizes to '} ' (trailing space) is NOT Text '}' in
            # Caddy -> inert content; str.strip wrongly reduced it to '}'.
            "respond <<X\n} \nX",
            # heredoc carrying a JSON body with balanced braces -> inert content.
            'respond <<JSON\n{"status": "ok", "nested": {"a": 1}}\nJSON',
            # The exact snippet a prior round wrongly flagged as an escape: the
            # heredoc body '{' OPENS a block (Text '{'), the bare '}' closes it,
            # then 'example.com {' opens and the '<<H2' heredoc '}' closes that --
            # every brace stays inside the site block, so Caddy CONTAINS it.
            "respond <<HD\n{\nHD\n}\nexample.com {\n\trespond x <<H2\n}\nH2",
        ],
    )
    def test_balanced_and_inert_heredoc_bodies_accepted(self, snippet):
        # Faithful finalizeHeredoc handling keeps legitimate heredoc snippets that
        # real Caddy accepts from being over-rejected.
        assert _make_create(custom_caddy_snippet=snippet).custom_caddy_snippet == snippet

    # ------------------------------------------------------------------
    # Round-5 escapes the round-4 heredoc fix still missed. Each snippet is
    # ACCEPTED by the pre-fix lexer yet escapes the per-service site block once
    # rendered by edge/config_renderer.py, so all MUST be rejected.
    # ------------------------------------------------------------------

    # config_renderer strips the snippet and re-lines it via str.splitlines,
    # normalising CR (and other Python line breaks) to LF. That ends a '#'
    # comment Caddy's raw lexer would have kept open, exposing the brace it hid.
    # The pre-fix lexer scanned the RAW snippet and missed the re-lining.
    @pytest.mark.parametrize(
        "snippet",
        [
            '#x\r"}"',                                       # CR ends the comment -> "}" escapes
            "reverse_proxy x  # note\r}",                    # CR re-lines a bare } out of the comment
            # Clean two-site injection (loads with NO parse garbage): the
            # comment-hidden '"}"' closes the site block, evil.com is defined as a
            # fully attacker-controlled TOP-LEVEL block, and a second
            # comment-hidden '"{"' absorbs the site block's real closing brace.
            "#x\r\"}\"\nevil.com {\n\treverse_proxy attacker:80\n}\n#y\r\"{\"",
        ],
    )
    def test_render_relining_brace_escape_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_render_relining_brace_escape_rejected_update(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(
                custom_caddy_snippet="#x\r\"}\"\nevil.com {\n\treverse_proxy x\n}\n#y\r\"{\""
            )

    # Caddy keeps a pending backslash across blank space when no token text has
    # accumulated, so '\ {' / '\<tab>{' lex to the literal token '\{' (inert),
    # NOT a '{' delimiter. The pre-fix lexer cleared the escape at the blank and
    # counted '\ {' as a real '{', so a following bare '}' (a genuine closer)
    # rebalanced its count and an escape slipped through.
    @pytest.mark.parametrize(
        "snippet",
        [
            "\\ {\n}",                                       # backslash-space, then a bare } closer
            "\\\t{\n}",                                      # backslash-tab variant
            "\\ {\n}\nevil.com {\n\treverse_proxy attacker:80\n}",
        ],
    )
    def test_backslash_blank_escape_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_backslash_blank_escape_rejected_update(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet="\\ {\n}")

    # An unterminated quoted string / backtick / heredoc never closes before the
    # rendered site block does, so it consumes the block's own closing brace
    # (a dangling-open escape). The pre-fix lexer dropped the partial token and
    # accepted these.
    @pytest.mark.parametrize(
        "snippet",
        [
            '"',                                             # unterminated double quote
            "`",                                             # unterminated backtick
            'respond "oops',                                 # unterminated quote carrying content
            '"}\t',                                          # .strip() trims the tail -> Caddy lexes "}" closer
            "respond <<EOF\nbody",                           # unterminated heredoc
        ],
    )
    def test_unterminated_string_or_heredoc_rejected_create(self, snippet):
        with pytest.raises(ValidationError):
            _make_create(custom_caddy_snippet=snippet)

    def test_unterminated_quote_rejected_update(self):
        with pytest.raises(ValidationError):
            ServiceUpdate(custom_caddy_snippet='respond "oops')

    # Regression: legitimate render-aware snippets real Caddy accepts must stay
    # accepted (no new over-rejection from the render-transform / faithful-escape
    # handling), including the heredoc class the round-4 fix un-blocked.
    @pytest.mark.parametrize(
        "snippet",
        [
            "respond <<HTML\n<html>{name}</html>\nHTML",
            'respond <<JSON\n{"a": {"b": 1}}\nJSON',
            "@r path_regexp ^/a{2,5}$",
            "header_up Host {host}",
            "respond \\}",                                   # backslash-escaped brace is inert in Caddy
            "route {\n\trespond <<MSG\n\thi\n\tMSG\n}",      # indented heredoc inside a block
            "reverse_proxy api:80  # trailing comment }",
        ],
    )
    def test_render_aware_legit_snippets_accepted(self, snippet):
        assert _make_create(custom_caddy_snippet=snippet).custom_caddy_snippet == snippet

    def test_render_snippet_block_matches_config_renderer(self):
        # Single-source-of-truth guard: render_caddyfile MUST embed the snippet
        # via render_snippet_block (the same function the validator lexes), so the
        # validated bytes always equal the deployed Caddyfile's snippet block.
        from types import SimpleNamespace

        from app.edge import config_renderer

        for v in [
            "respond <<HTML\n<h1>{x}</h1>\nHTML",
            "a\rb\rc",
            "  spaced  ",
            "line\vvt\fff",
            "header_up Host {host}",
        ]:
            svc = SimpleNamespace(
                hostname="app.example.com",
                upstream_container_name="backend",
                upstream_port=80,
                upstream_scheme="http",
                preserve_host_header=True,
                custom_caddy_snippet=v,
            )
            assert config_renderer.render_snippet_block(v) in config_renderer.render_caddyfile(svc)


# ---------------------------------------------------------------------------
# ServiceResponse datetime serialization (dead from_attributes config removed)
# ---------------------------------------------------------------------------


class TestServiceResponseSerialization:
    def test_datetimes_serialize_as_iso_strings(self):
        # created_at/updated_at remain str isoformat (the working contract);
        # removing the unused from_attributes config must not change this.
        resp = ServiceResponse(
            id="svc_1",
            name="App",
            enabled=True,
            upstream_container_id="abc123",
            upstream_container_name="app",
            upstream_scheme="http",
            upstream_port=80,
            hostname="app.example.com",
            base_domain="example.com",
            edge_container_name="edge_app",
            network_name="edge_net_app",
            ts_hostname="edge-app",
            preserve_host_header=True,
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-02T00:00:00+00:00",
        )
        dumped = resp.model_dump()
        assert dumped["created_at"] == "2026-01-01T00:00:00+00:00"
        assert dumped["updated_at"] == "2026-01-02T00:00:00+00:00"
        assert isinstance(dumped["created_at"], str)
