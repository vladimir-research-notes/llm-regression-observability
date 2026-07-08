"""A tiny, deliberately *restricted* YAML reader (standard library only).

The core package must stay dependency-free (see ``CLAUDE.md`` / ``docs/PLAN.md``
§3), so it cannot take a ``PyYAML`` dependency. Suite manifests
(``evals/suites/*.yaml``) are, however, human-authored configuration, and YAML is
more readable for that than JSON. This module parses the *small, explicit subset*
of YAML those manifests use — and nothing else.

Supported:

* A document that is a block **mapping** at indent 0.
* ``key: scalar`` and ``key:`` followed by an indented child block.
* Block **sequences** of scalars (``- item``), indented deeper than their key.
* Nested block **mappings**, indented deeper than their key.
* Scalars: single/double-quoted strings, ``null``/``~``, ``true``/``false``
  (case-insensitive), integers, floats, and bare strings.
* ``#`` comments (whole-line and inline after whitespace, outside quotes).

**Not** supported (raises :class:`YamlError`): flow style (``{}`` / ``[]``),
anchors/aliases/tags, multi-document streams, block scalars (``|`` / ``>``),
sequences of mappings, and tabs for indentation. This is intentional: the reader
should fail loudly rather than silently misread an unsupported construct. It is
not a general YAML implementation.
"""

from __future__ import annotations

from typing import Any

__all__ = ["YamlError", "load_yaml"]


class YamlError(ValueError):
    """Raised when input is malformed or uses an unsupported YAML construct."""


# One physical line, reduced to (indentation, content) with comments stripped.
_Line = tuple[int, str, int]  # (indent, content, source_line_number)


def _strip_inline_comment(text: str) -> str:
    """Remove a trailing ``# ...`` comment that is outside any quotes."""
    quote: str | None = None
    for i, ch in enumerate(text):
        if quote is not None:
            if ch == quote:
                quote = None
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _tokenize(text: str) -> list[_Line]:
    lines: list[_Line] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw[: len(raw) - len(raw.lstrip(" "))]:
            raise YamlError(f"line {lineno}: tab in indentation is not supported")
        indent = len(raw) - len(raw.lstrip(" "))
        body = raw.strip()
        if not body or body.startswith("#"):
            continue
        content = _strip_inline_comment(body)
        if content:
            lines.append((indent, content, lineno))
    return lines


def _parse_scalar(token: str, lineno: int) -> Any:
    if not token:
        return None
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    if token in ("~", "null", "Null", "NULL"):
        return None
    low = token.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if token in ("{}", "[]") or token[0] in "{[&*!|>":
        raise YamlError(f"line {lineno}: unsupported YAML construct {token!r}")
    try:
        return int(token)
    except ValueError:
        pass
    try:
        return float(token)
    except ValueError:
        pass
    return token


def _parse_block(lines: list[_Line], start: int, indent: int) -> tuple[Any, int]:
    """Parse the block whose members sit at column ``indent``.

    Returns the parsed value and the index of the first line that does not
    belong to the block.
    """
    if lines[start][1].startswith("- "):
        return _parse_sequence(lines, start, indent)
    return _parse_mapping(lines, start, indent)


def _parse_sequence(lines: list[_Line], start: int, indent: int) -> tuple[list, int]:
    items: list[Any] = []
    i = start
    while i < len(lines):
        line_indent, content, lineno = lines[i]
        if line_indent != indent:
            break
        if not content.startswith("- "):
            raise YamlError(f"line {lineno}: expected a '- ' sequence item")
        item = content[2:].strip()
        if not item:
            raise YamlError(
                f"line {lineno}: empty / nested sequence items are not supported"
            )
        if ":" in item and item[0] not in ("'", '"'):
            raise YamlError(
                f"line {lineno}: sequences of mappings are not supported"
            )
        items.append(_parse_scalar(item, lineno))
        i += 1
    return items, i


def _parse_mapping(lines: list[_Line], start: int, indent: int) -> tuple[dict, int]:
    mapping: dict[str, Any] = {}
    i = start
    while i < len(lines):
        line_indent, content, lineno = lines[i]
        if line_indent != indent:
            break
        if content.startswith("- "):
            raise YamlError(f"line {lineno}: sequence item inside a mapping")
        if ":" not in content:
            raise YamlError(f"line {lineno}: expected 'key: value', got {content!r}")
        key_part, _, value_part = content.partition(":")
        key = key_part.strip()
        if len(key) >= 2 and key[0] == key[-1] and key[0] in ("'", '"'):
            key = key[1:-1]
        if key in mapping:
            raise YamlError(f"line {lineno}: duplicate key {key!r}")
        value_token = value_part.strip()
        if value_token:
            mapping[key] = _parse_scalar(value_token, lineno)
            i += 1
            continue
        # Nested block: its members must be indented deeper than this key.
        if i + 1 < len(lines) and lines[i + 1][0] > indent:
            child_indent = lines[i + 1][0]
            value, i = _parse_block(lines, i + 1, child_indent)
            mapping[key] = value
        else:
            mapping[key] = None
            i += 1
    return mapping, i


def load_yaml(text: str) -> Any:
    """Parse the restricted-YAML ``text`` into Python data.

    Raises :class:`YamlError` on malformed input or an unsupported construct.
    """
    lines = _tokenize(text)
    if not lines:
        return None
    if lines[0][0] != 0:
        raise YamlError(f"line {lines[0][2]}: document must start at column 0")
    value, consumed = _parse_block(lines, 0, 0)
    if consumed != len(lines):
        stray = lines[consumed]
        raise YamlError(
            f"line {stray[2]}: unexpected indentation for {stray[1]!r}"
        )
    return value
