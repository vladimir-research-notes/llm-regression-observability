"""Redaction: no credential-like or PII-like string reaches an artifact.

``docs/SAFETY.md`` states the guarantee this module implements — a secret-shaped
string is never written to a trace, a span, or a report. Milestone 7 built the
*gate* (fail-closed, on the rendered report) and left the *mask* to this module.
Both now exist, and they are deliberately not the same rule set.

**One table, every caller.** :data:`RULES` is the single definition of
"credential-like" in this repository. ``reports/base.py`` (the report gate),
``datasets/validation.py`` (``llmreg validate-fixtures``), ``observability/spans.py``
(the span writer) and :mod:`..executor` (the trace writer) all read it from here. A
second copy of a pattern table is a false sense of safety, because the copy that
misses a pattern is the one that runs.

**Two tiers, because refusing and masking carry different costs.**

``certain``
    Patterns that cannot plausibly occur in prose: provider key prefixes
    (``sk-``, ``AKIA``, ``ghp_``, ``AIza``, ``xox…``), a JWT, a private-key header,
    a ``Bearer`` credential, an ``Authorization`` header. A match is a secret. These
    are the rules the **gate** refuses on, and the rules that decide
    ``redaction_status``.

``heuristic``
    Patterns that *can* fire on legitimate text: a credential-named assignment
    (``api_key=…``), an email address, a generic high-entropy token. These rules
    **mask but never refuse**. A false-positive refusal is a denial of service on
    your own report; a false-positive mask costs one mangled string in a synthetic
    fixture, and :attr:`RunRecord.redaction_status` records that it happened.

The asymmetry is the point. Refusal is reserved for evidence; masking is applied
on suspicion.

**Identity fields are scanned, never rewritten.** ``run_id``, ``case_id``,
``model_id``, a ``tool_calls[].name``, a ``retrieval_sources[].source_id``, an
``errors[].kind`` — these are join keys. The comparison engine pairs cases by
``case_id`` and the tool evaluator diffs by tool *name*; masking one would silently
destroy the artifact's meaning while claiming to protect it. So a certain-tier
match in an identity field yields ``redaction_status="flagged"``, and the executor
**refuses to persist the run** rather than write a secret or corrupt a key. That is
what ``flagged`` means and why it can never appear in a file this project wrote —
it is reachable only on a trace *imported* from elsewhere (Milestone 10).

Content fields — ``final_output``, ``parsed_output``, tool-call arguments, an error
*message*, a retrieved snippet — are masked in place with
``[REDACTED:<rule>]``. The placeholder matches no rule, so the post-condition
(:func:`redact_record` re-scans what it masked) holds.

**The status is derived, and it only moves one way.** ``clean < redacted <
flagged``; :func:`redact_record` takes the maximum of what *this* pass found and
what the record already carried. A record arriving with ``redaction_status="clean"``
over a live secret is overruled, because the scan is the authority and that is the
dangerous lie. A record arriving as ``redacted`` keeps it even though a second pass
finds nothing left to mask — its text is full of placeholders, and downgrading it to
``clean`` would erase the one fact the field exists to record. The only lie the
upper two statuses can tell is a conservative one, so they are preserved and the
monotone rule makes redaction idempotent: ``redact_record(redact_record(r))`` is
``redact_record(r)``, bytes and status alike.

**Nothing here echoes a secret.** A finding is ``(line, rule_name)``. The value is
never returned, logged, or put in an exception message — an error message is a
place secrets leak too.

Deterministic: no clock, no RNG, no network. Redacting the same record twice
yields the same bytes, and redacting an already-redacted record is a no-op.
"""

from __future__ import annotations

import dataclasses
import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..schemas import ErrorRecord, RetrievalTrace, RunRecord, ToolCallTrace

__all__ = [
    "RedactionError",
    "Redactor",
    "RedactionRule",
    "TIER_CERTAIN",
    "TIER_HEURISTIC",
    "RULES",
    "CERTAIN_RULES",
    "SECRET_PATTERNS",
    "MASK",
    "CLEAN",
    "REDACTED",
    "FLAGGED",
    "REDACTION_STATUSES",
    "STATUS_ORDER",
    "escalate",
    "IDENTITY_FIELDS",
    "CONTENT_FIELDS",
    "NON_TEXT_FIELDS",
    "shannon_entropy",
    "scan_text",
    "scan_for_secrets",
    "secret_findings",
    "assert_clean",
    "redact_text",
    "redact_value",
    "identity_findings",
    "RecordRedaction",
    "redact_record",
    "ALLOW_MARKER",
    "TreeFinding",
    "scan_tree",
]


class RedactionError(RuntimeError):
    """Raised when an artifact cannot be redacted, or must not be written."""


#: A text-in, text-out masking function. :func:`redact_text` is the one that ships;
#: the type is what ``reports.build_report()`` takes so a caller can substitute one.
Redactor = Callable[[str], str]


# --------------------------------------------------------------------------- #
# Statuses and record shape
# --------------------------------------------------------------------------- #

#: ``RunRecord.redaction_status`` — derived by :func:`redact_record`, never claimed
#: by an adapter. See the module docstring for what ``flagged`` costs a run.
CLEAN = "clean"
REDACTED = "redacted"
FLAGGED = "flagged"
REDACTION_STATUSES: tuple[str, ...] = (CLEAN, REDACTED, FLAGGED)

#: The status lattice. Redaction takes the maximum of what it found and what the
#: record already claimed, so it can only ever escalate. See the module docstring.
STATUS_ORDER: dict[str, int] = {CLEAN: 0, REDACTED: 1, FLAGGED: 2}


def escalate(*statuses: str) -> str:
    """The most severe of ``statuses`` under :data:`STATUS_ORDER`."""
    return max(statuses, key=STATUS_ORDER.__getitem__)


# Every ``RunRecord`` field is classified exactly once below. The partition is not
# decoration: ``test_every_run_record_field_is_classified`` asserts it covers the
# dataclass, so adding a field to the schema fails the suite until somebody decides
# whether the redactor may rewrite it. An unclassified field is a field nobody
# decided about, which is how a secret reaches disk.

#: ``RunRecord`` fields that identify the run, the case, or a step within it.
#: Scanned with the certain rules; never rewritten. Nested identity fields
#: (``tool_calls[].name``, ``retrieval_sources[].source_id``, ``errors[].kind``) are
#: walked by :func:`identity_findings`.
IDENTITY_FIELDS: tuple[str, ...] = (
    "run_id",
    "case_id",
    "app_version",
    "model_id",
    "prompt_version",
    "tool_version",
    "timestamp",
)

#: ``RunRecord`` fields that carry model- or tool-produced text. Masked in place.
CONTENT_FIELDS: tuple[str, ...] = (
    "final_output",
    "parsed_output",
    "tool_calls",
    "retrieval_sources",
    "errors",
)

#: ``RunRecord`` fields that hold no string a model or a tool could have written:
#: counts, durations, prices, and the status this module derives. Nothing to redact.
NON_TEXT_FIELDS: tuple[str, ...] = (
    "input_tokens_estimate",
    "output_tokens_estimate",
    "latency_ms",
    "cost_estimate_usd",
    "redaction_status",
)

#: What a masked span of text is replaced with. Contains ``[`` and ``:``, neither of
#: which appears in any rule's character class, so a placeholder never re-matches.
MASK = "[REDACTED:{rule}]"


# --------------------------------------------------------------------------- #
# The rule table
# --------------------------------------------------------------------------- #

TIER_CERTAIN = "certain"
TIER_HEURISTIC = "heuristic"

#: A generic token needs at least this many characters before entropy is even
#: considered. Short mixed-case identifiers are far more common than short secrets.
MIN_TOKEN_LENGTH = 24

#: Shannon entropy floor, in bits per character, for the generic-token rule. A
#: 24-character random alphanumeric string sits near 4.3; English prose near 2.
MIN_ENTROPY_BITS = 3.2


def shannon_entropy(text: str) -> float:
    """Shannon entropy of ``text`` in bits per character (``0.0`` when empty)."""
    if not text:
        return 0.0
    length = len(text)
    return -sum(
        (count / length) * math.log2(count / length)
        for count in Counter(text).values()
    )


_LOWER = frozenset("abcdefghijklmnopqrstuvwxyz")
_UPPER = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ")
_DIGIT = frozenset("0123456789")


def _looks_high_entropy(token: str) -> bool:
    """Three filters, cheapest first, then entropy.

    Mixed character classes are required because the false positives that matter
    here are all single-class: a SHA-256 digest (lowercase hex — and this project
    writes one into every artifact), a UUID, a snake_case identifier, a hyphenated
    run id. Requiring at least one lowercase, one uppercase and one digit clears all
    four without a length or entropy argument.
    """
    if len(token) < MIN_TOKEN_LENGTH:
        return False
    chars = set(token)
    if not (chars & _LOWER and chars & _UPPER and chars & _DIGIT):
        return False
    return shannon_entropy(token) >= MIN_ENTROPY_BITS


@dataclass(frozen=True)
class RedactionRule:
    """One pattern, what it is called, and how much the project trusts it.

    ``group`` names the sub-match to replace, so a rule can keep its context: the
    ``Authorization`` rule masks the credential and leaves the header name, which is
    the difference between a readable trace and a useless one. ``predicate`` lets a
    rule refine a match it cannot express as a regex (entropy).
    """

    name: str
    tier: str
    pattern: re.Pattern[str]
    group: int = 0
    predicate: Callable[[str], bool] | None = None
    why: str = ""

    @property
    def certain(self) -> bool:
        return self.tier == TIER_CERTAIN

    def matches(self, text: str) -> bool:
        return any(True for _ in self.finditer(text))

    def finditer(self, text: str) -> Iterable[re.Match[str]]:
        """Matches surviving :attr:`predicate`."""
        for match in self.pattern.finditer(text):
            if self.predicate is None or self.predicate(match.group(self.group)):
                yield match

    def mask(self, text: str) -> tuple[str, int]:
        """Return ``(masked_text, replacements)``. Only :attr:`group` is replaced."""
        count = 0

        def _replace(match: re.Match[str]) -> str:
            nonlocal count
            captured = match.group(self.group)
            if self.predicate is not None and not self.predicate(captured):
                return match.group(0)
            count += 1
            offset = match.start()
            start, end = match.span(self.group)
            whole = match.group(0)
            return (
                whole[: start - offset]
                + MASK.format(rule=self.name)
                + whole[end - offset :]
            )

        return self.pattern.sub(_replace, text), count


# The order is the order rules are applied, and it is load bearing: a specific rule
# must mask before a general one, or the general rule claims the credit (and a
# reader loses the provider name). The placeholder cannot re-match, so a later rule
# never masks an earlier rule's output.
RULES: tuple[RedactionRule, ...] = (
    RedactionRule(
        name="private_key",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
        why="PEM private-key header",
    ),
    RedactionRule(
        name="openai_key",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"sk-[A-Za-z0-9]{16,}"),
        why="provider-style API key",
    ),
    RedactionRule(
        name="aws_access_key",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"AKIA[0-9A-Z]{16}"),
        why="AWS access key id",
    ),
    RedactionRule(
        name="github_token",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        why="GitHub personal-access / app token",
    ),
    RedactionRule(
        name="google_api_key",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
        why="Google API key",
    ),
    RedactionRule(
        name="slack_token",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
        why="Slack token",
    ),
    RedactionRule(
        name="jwt",
        tier=TIER_CERTAIN,
        pattern=re.compile(
            r"eyJ[A-Za-z0-9_\-]{8,}\.eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"
        ),
        why="JSON Web Token (header.payload.signature)",
    ),
    # Masks the credential, keeps the header name. Runs before ``bearer_token`` so
    # ``Authorization: Bearer …`` is attributed to the header it arrived in.
    RedactionRule(
        name="authorization_header",
        tier=TIER_CERTAIN,
        pattern=re.compile(
            r"(?i)\bauthorization\b[ \t]*:[ \t]*(?:[A-Za-z]+[ \t]+)?"
            r"([A-Za-z0-9._\-+/=]{8,})"
        ),
        group=1,
        why="Authorization request header",
    ),
    RedactionRule(
        name="bearer_token",
        tier=TIER_CERTAIN,
        pattern=re.compile(r"\bBearer[ \t]+([A-Za-z0-9._\-]{20,})"),
        group=1,
        why="bearer credential outside an Authorization header",
    ),
    # ---- heuristic: mask, never refuse ------------------------------------- #
    RedactionRule(
        name="secret_assignment",
        tier=TIER_HEURISTIC,
        pattern=re.compile(
            r"(?i)\b(?:api[_\-]?key|secret(?:[_\-]?(?:key|token))?|password|passwd"
            r"|access[_\-]?key|(?:api|auth|access|bearer)[_\-]?token)\b"
            r"[ \t]*[:=][ \t]*[\"']?([^\s\"',;}\[\]]{6,})"
        ),
        group=1,
        why="a credential-named key with a value",
    ),
    RedactionRule(
        name="email",
        tier=TIER_HEURISTIC,
        pattern=re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"),
        why="email address (PII, not a credential)",
    ),
    RedactionRule(
        name="high_entropy_token",
        tier=TIER_HEURISTIC,
        pattern=re.compile(rf"[A-Za-z0-9+/=_\-]{{{MIN_TOKEN_LENGTH},}}"),
        predicate=_looks_high_entropy,
        why="long mixed-class token with entropy above the prose floor",
    ),
)

#: The rules a match of which is, on its own, sufficient evidence of a secret. The
#: report gate refuses on these; ``redaction_status`` is decided by these; the
#: repository-hygiene scan reports these.
CERTAIN_RULES: tuple[RedactionRule, ...] = tuple(
    rule for rule in RULES if rule.certain
)

#: Back-compatible view of :data:`CERTAIN_RULES` as ``(name, pattern)`` pairs. Kept
#: because ``datasets/validation.py`` and ``reports/base.py`` published this name
#: before the table moved here; both now import it from this module.
SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (rule.name, rule.pattern) for rule in CERTAIN_RULES
)


# --------------------------------------------------------------------------- #
# Scanning
# --------------------------------------------------------------------------- #


def scan_text(
    text: str, rules: Sequence[RedactionRule] = CERTAIN_RULES
) -> tuple[str, ...]:
    """Names of every rule in ``rules`` that fires on ``text``, in table order."""
    return tuple(rule.name for rule in rules if rule.matches(text))


def scan_for_secrets(text: str) -> list[str]:
    """Names of the certain-tier patterns found in ``text``.

    The spelling ``datasets/validation.py`` and ``llmreg validate-fixtures`` have
    used since Milestone 2, kept so a fixture check reads the same table as the
    report gate.
    """
    return list(scan_text(text))


def secret_findings(
    text: str, rules: Sequence[RedactionRule] = CERTAIN_RULES
) -> tuple[tuple[int, str], ...]:
    """Every ``(line_number, rule_name)`` where ``text`` looks credential-like.

    The match itself is deliberately not returned. Callers report *that* a secret is
    present and *where*; a caller that wanted the value would be reintroducing the
    leak it is trying to prevent.
    """
    found: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        found.extend((lineno, rule.name) for rule in rules if rule.matches(line))
    return tuple(found)


def refusal_message(source: str, findings: Sequence[tuple[int, str]]) -> str:
    """The one wording for "this artifact is not being written". Names no value."""
    where = ", ".join(f"line {lineno} ({name})" for lineno, name in findings)
    return (
        f"{source}: refusing to write an artifact with {len(findings)} "
        f"unredacted credential-like string(s): {where}. "
        "Redact the source trace, or pass a masking redactor."
    )


def assert_clean(
    text: str,
    *,
    source: str = "artifact",
    error: type[Exception] = RedactionError,
) -> None:
    """Refuse to emit ``text`` if any certain-tier pattern survived redaction.

    Fail-closed, and the last thing that runs before bytes hit the disk. Heuristic
    rules are not consulted: they mask on suspicion, and a suspicion must not be
    able to block an artifact.
    """
    findings = secret_findings(text)
    if findings:
        raise error(refusal_message(source, findings))


# --------------------------------------------------------------------------- #
# Masking
# --------------------------------------------------------------------------- #


def redact_text(text: str, rules: Sequence[RedactionRule] = RULES) -> str:
    """Mask every rule match in ``text``. The :data:`Redactor` that ships."""
    return _redact_counted(text, rules)[0]


def _redact_counted(
    text: str, rules: Sequence[RedactionRule] = RULES
) -> tuple[str, tuple[str, ...]]:
    """``(masked_text, names_of_rules_that_fired)`` — in table order."""
    fired: list[str] = []
    for rule in rules:
        text, count = rule.mask(text)
        if count:
            fired.append(rule.name)
    return text, tuple(fired)


def redact_value(value: Any, rules: Sequence[RedactionRule] = RULES) -> Any:
    """Mask every string reachable from ``value`` — dict keys included.

    A key inside ``parsed_output`` or a tool call's ``arguments`` is *model-produced*
    text, not schema: the model chose it. ``observability/spans.py`` copies argument
    keys into telemetry on the grounds that they are schema, and this is what makes
    that safe.
    """
    return _redact_value_counted(value, rules)[0]


def _redact_value_counted(
    value: Any, rules: Sequence[RedactionRule]
) -> tuple[Any, tuple[str, ...]]:
    if isinstance(value, str):
        return _redact_counted(value, rules)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        dict_fired: list[str] = []
        for key, item in value.items():
            new_key, key_fired = _redact_value_counted(key, rules)
            new_item, item_fired = _redact_value_counted(item, rules)
            out[new_key] = new_item
            dict_fired.extend(key_fired)
            dict_fired.extend(item_fired)
        return out, tuple(dict_fired)
    if isinstance(value, list):
        out_list: list[Any] = []
        list_fired: list[str] = []
        for item in value:
            new_item, item_fired = _redact_value_counted(item, rules)
            out_list.append(new_item)
            list_fired.extend(item_fired)
        return out_list, tuple(list_fired)
    return value, ()


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


def identity_findings(record: RunRecord) -> tuple[tuple[str, str], ...]:
    """``(field_path, rule_name)`` for every certain-tier match in a join key.

    Sorted by field path so the refusal message is stable. A heuristic match is not
    reported: masking is not an option for these fields, so a suspicion could only
    ever produce a false refusal.
    """
    found: list[tuple[str, str]] = []
    for name in IDENTITY_FIELDS:
        for rule_name in scan_text(getattr(record, name)):
            found.append((name, rule_name))
    for index, call in enumerate(record.tool_calls):
        for rule_name in scan_text(call.name):
            found.append((f"tool_calls[{index}].name", rule_name))
    for index, source in enumerate(record.retrieval_sources):
        for rule_name in scan_text(source.source_id):
            found.append((f"retrieval_sources[{index}].source_id", rule_name))
    for index, error in enumerate(record.errors):
        for rule_name in scan_text(error.kind):
            found.append((f"errors[{index}].kind", rule_name))
    return tuple(sorted(found))


@dataclass(frozen=True)
class RecordRedaction:
    """The outcome of redacting one :class:`~..schemas.RunRecord`.

    ``record`` always carries the derived :attr:`status`, so a caller that ignores
    everything else still persists an honest artifact. A caller that must not persist
    a ``flagged`` record — the executor — reads :attr:`identity` to say which join
    key holds the secret, without echoing it.
    """

    record: RunRecord
    status: str
    masked: tuple[str, ...] = ()
    identity: tuple[tuple[str, str], ...] = ()

    @property
    def clean(self) -> bool:
        return self.status == CLEAN

    @property
    def flagged(self) -> bool:
        return self.status == FLAGGED

    def describe(self) -> str:
        """Why this record is ``flagged``, in one line, naming no value."""
        if not self.identity:
            return (
                f"case {self.record.case_id!r}: the source trace arrived with "
                f"redaction_status={FLAGGED!r}. A flagged record is never persisted; "
                "redact it at its origin."
            )
        where = ", ".join(f"{field} ({rule})" for field, rule in self.identity)
        return (
            f"case {self.record.case_id!r}: credential-like string in identity "
            f"field(s) {where}. An identity field is a join key and is never "
            "rewritten; fix the source trace."
        )


def _redact_tool_call(
    call: ToolCallTrace, rules: Sequence[RedactionRule]
) -> tuple[ToolCallTrace, tuple[str, ...]]:
    arguments, arg_fired = _redact_value_counted(call.arguments, rules)
    error, err_fired = (
        _redact_counted(call.error, rules) if call.error else (call.error, ())
    )
    return (
        dataclasses.replace(call, arguments=arguments, error=error),
        arg_fired + err_fired,
    )


def _redact_retrieval(
    source: RetrievalTrace, rules: Sequence[RedactionRule]
) -> tuple[RetrievalTrace, tuple[str, ...]]:
    snippet, snippet_fired = _redact_counted(source.snippet, rules)
    claim, claim_fired = (
        _redact_counted(source.claim, rules) if source.claim else (source.claim, ())
    )
    return (
        dataclasses.replace(source, snippet=snippet, claim=claim),
        snippet_fired + claim_fired,
    )


def _redact_error(
    error: ErrorRecord, rules: Sequence[RedactionRule]
) -> tuple[ErrorRecord, tuple[str, ...]]:
    message, fired = _redact_counted(error.message, rules)
    return dataclasses.replace(error, message=message), fired


def redact_record(
    record: RunRecord, rules: Sequence[RedactionRule] = RULES
) -> RecordRedaction:
    """Mask a record's content, scan its identity, and derive ``redaction_status``.

    Pure and idempotent, bytes and status alike. The status is *derived here* and
    escalated against whatever the record already claimed: an adapter's ``clean`` over
    a live secret is overruled, and its ``redacted`` is kept. A trace that claims to
    be clean is a claim, and this is the only place that can check it.
    """
    identity = identity_findings(record)

    final_output, output_fired = _redact_counted(record.final_output, rules)
    parsed_output, parsed_fired = _redact_value_counted(record.parsed_output, rules)

    tool_calls: list[ToolCallTrace] = []
    retrieval_sources: list[RetrievalTrace] = []
    errors: list[ErrorRecord] = []
    fired: list[str] = [*output_fired, *parsed_fired]
    for call in record.tool_calls:
        new_call, call_fired = _redact_tool_call(call, rules)
        tool_calls.append(new_call)
        fired.extend(call_fired)
    for source in record.retrieval_sources:
        new_source, source_fired = _redact_retrieval(source, rules)
        retrieval_sources.append(new_source)
        fired.extend(source_fired)
    for error in record.errors:
        new_error, error_fired = _redact_error(error, rules)
        errors.append(new_error)
        fired.extend(error_fired)

    hits = set(fired)
    masked = tuple(rule.name for rule in rules if rule.name in hits)
    if identity:
        found = FLAGGED
    elif masked:
        found = REDACTED
    else:
        found = CLEAN
    # Monotone: a prior `redacted`/`flagged` survives a pass that finds nothing new.
    status = escalate(found, record.redaction_status)

    redacted = dataclasses.replace(
        record,
        final_output=final_output,
        parsed_output=parsed_output,
        tool_calls=tool_calls,
        retrieval_sources=retrieval_sources,
        errors=errors,
        redaction_status=status,
    )
    _assert_content_masked(redacted)
    return RecordRedaction(
        record=redacted, status=status, masked=masked, identity=identity
    )


def _content_text(record: RunRecord) -> str:
    """Every string the redactor is responsible for, as one blob, for re-scanning.

    Identity fields are excluded on purpose: a secret in one of those is a
    ``flagged`` record, which is the executor's refusal to make, not a broken mask.
    """
    parts = [record.final_output, _strings(record.parsed_output)]
    for call in record.tool_calls:
        parts.append(_strings(call.arguments))
        parts.append(call.error or "")
    for source in record.retrieval_sources:
        parts.append(source.snippet)
        parts.append(source.claim or "")
    parts.extend(error.message for error in record.errors)
    return "\n".join(parts)


def _strings(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(
            f"{_strings(k)}\n{_strings(v)}" for k, v in sorted(value.items(), key=str)
        )
    if isinstance(value, list):
        return "\n".join(_strings(item) for item in value)
    return ""


def _assert_content_masked(record: RunRecord) -> None:
    """Post-condition: nothing certain-tier survives in a content field.

    A rule whose replacement still matches its own pattern would loop or leak. This
    catches that at the point of change rather than at the report gate, three stages
    downstream.
    """
    findings = secret_findings(_content_text(record))
    if findings:
        raise RedactionError(
            refusal_message(f"case {record.case_id!r} (post-redaction)", findings)
        )


# --------------------------------------------------------------------------- #
# Repository hygiene
# --------------------------------------------------------------------------- #

#: A line carrying this marker is skipped by :func:`scan_tree`. Test fixtures and
#: documentation legitimately need to *name* a credential shape; the alternative —
#: excluding ``tests/`` from the scan — would exclude the files most likely to grow
#: a real key by accident.
ALLOW_MARKER = "redaction:allow"

#: Directories the scan never descends into: version control, caches, virtualenvs,
#: and ``results/`` (regenerable output, gitignored).
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "__pycache__",
        ".venv",
        "venv",
        "env",
        "ENV",
        "node_modules",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        "build",
        "dist",
        "results",
    }
)

#: Files skipped by name: gitignored personal configuration, never committed.
SKIP_NAMES: frozenset[str] = frozenset({"settings.local.json"})

#: Suffixes the scan reads. Everything else is assumed binary or generated. ``""``
#: covers dotfiles such as ``.gitignore`` and extensionless files such as ``LICENSE``.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {
        "",
        ".cfg",
        ".css",
        ".html",
        ".ini",
        ".json",
        ".jsonl",
        ".md",
        ".py",
        ".rst",
        ".sh",
        ".toml",
        ".txt",
        ".yaml",
        ".yml",
    }
)

#: Files larger than this are not scanned. Nothing this repository commits is close.
MAX_SCAN_BYTES = 1_048_576


@dataclass(frozen=True)
class TreeFinding:
    """A credential-like string found in a committed file. Carries no value."""

    path: str
    line: int
    rule: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: credential-like pattern {self.rule!r}"


def _scannable(path: Path) -> bool:
    if path.name in SKIP_NAMES or path.suffix not in TEXT_SUFFIXES:
        return False
    try:
        return path.stat().st_size <= MAX_SCAN_BYTES
    except OSError:  # pragma: no cover - defensive
        return False


def scan_tree(root: Path | str) -> tuple[TreeFinding, ...]:
    """Scan a source tree for accidentally committed credentials.

    Certain-tier rules only. The heuristic tier would flag every SHA-256 digest and
    every author email in ``pyproject.toml``, and a hygiene check that cries wolf is
    a hygiene check that gets skipped. Emails and generic tokens are a *trace*
    concern, where masking is free; a repository is scanned for evidence.

    Findings are sorted by ``(path, line, rule)`` so two scans of one tree agree.
    """
    root = Path(root)
    findings: list[TreeFinding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts[:-1]):
            continue
        if not _scannable(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        relative = path.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            if ALLOW_MARKER in line:
                continue
            for rule in CERTAIN_RULES:
                if rule.matches(line):
                    findings.append(
                        TreeFinding(path=relative, line=lineno, rule=rule.name)
                    )
    return tuple(sorted(findings, key=lambda f: (f.path, f.line, f.rule)))
