#!/usr/bin/env python3
"""harvest — memory-harvest automation: run closeout -> shared-brain lessons.

The manual protocol (each run's 19-memory-harvest.md) stays the source of truth;
this automates the mechanical half: extract candidate lessons, dedupe against the
shared brain, and stage them as ready-to-append JSONL for human/agent approval.
Proposals are DATA (like plans) — nothing enters the brain without `apply`.

Usage:
  python3 scripts/harvest.py status                 # which done runs are unharvested
  python3 scripts/harvest.py scan  <run>            # extract + dedupe -> proposals JSONL
  python3 scripts/harvest.py apply <proposals.jsonl>  # append via brain_append, mark run

Extraction sources, in order:
  1. runs/<run>/19-memory-harvest.md   (## Lessons / User preferences / Project patterns / Safeguards)
  2. runs/<run>/12-evaluation-log.md   (table rows mentioning fail/revise — fallback only)

Dedupe: normalized (lowercase alnum) containment either way vs every shared-brain line.
"""
import os, re, sys, json, glob, datetime, secrets, stat, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
_SCRIPTS = os.path.join(ROOT, "scripts")
if os.path.isfile(os.path.join(_SCRIPTS, "brain_paths.py")):
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    import brain_paths
    SHARED_BRAIN = str(brain_paths.resolve_shared_brain(ROOT))
else:
    # The portable harvester copy must remain usable without core scripts.
    SHARED_BRAIN = os.environ.get(
        "PROJECT_OS_SHARED_BRAIN",
        os.path.join(os.path.expanduser("~"), ".project-os", "central-brain",
                     "shared-brain.jsonl"))
RUNS = os.path.join(ROOT, "runs")


def _run_roots():
    """Every runs/ tree this harvester should see.

    The public template defaults to its own runs directory. Configure any
    additional, private workspaces with PROJECT_OS_RUN_ROOTS (an
    os.pathsep-separated list) in the environment used by both the scheduler
    and interactive shell. The variable replaces the default list so every
    execution context sees the same roots.
    """
    env = os.environ.get("PROJECT_OS_RUN_ROOTS")
    if env:
        cands = [p for p in env.split(os.pathsep) if p.strip()]
    else:
        cands = [RUNS]
    roots, seen = [], set()
    for c in cands:
        c = os.path.abspath(os.path.expanduser(c))
        if c in seen or not os.path.isdir(c):
            continue
        seen.add(c)
        roots.append(c)
    return roots or [os.path.abspath(RUNS)]
PACKETS = os.path.join(ROOT, "blackboard", "packets")
MARKER = ".harvested"
SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

SECTION_TYPES = {  # 19-memory-harvest.md heading prefix -> shared-brain type
    # Three heading dialects exist in the wild: plural ("## Lessons"), singular
    # kebab ("## lesson"), and the public template's long forms ("## User
    # Preferences Observed", "## Next-Kickoff Safeguards"). Match all of them —
    # a missed heading silently drops lessons (bit us twice on 2026-07-02).
    "lesson": "lesson",
    "user preference": "preference",
    "user-preference": "preference",
    "project pattern": "pattern",
    "project-pattern": "pattern",
    "safeguard": "safeguard",
    "next-kickoff safeguard": "safeguard",
}
# Deciding "is this a rejection marker or a lesson that says the word reject?"
# by text alone is not solvable, and three successive regexes proved it:
#   v1 `\breject|private[- ]only\b`  -> dropped every lesson containing "reject"
#   v2 fully end-anchored               -> let "Rejected (see note)" harvest through
#   v3 continuation-word allowlist      -> still missed "Rejected because ...",
#                                          and swallowed "Reject-first workflow ..."
# So stop guessing and use the STRUCTURE instead (adversarial verify 2026-07-25):
#
#   * In a TABLE, the lesson is the FIRST cell and a verdict lives in a LATER
#     column. Later columns are therefore judged permissively: a standalone
#     marker ANYWHERE in them is a verdict ("No — rejected for reuse").
#   * The FIRST cell and a BULLET are free prose with no status column, so only
#     an EXPLICIT annotation counts there: a directive that is never ordinary
#     prose ("do not harvest", "private-only"), a marker followed by real
#     annotation punctuation ("Rejected: dupe", "[Rejected] ..."), or a cell
#     that is NOTHING BUT the marker ("Rejected", "**REJECTED**").
#
# The first cell is checked too. Restricting the check to cells[1:] (fix round 1,
# 2026-07-25) was more permissive than the bug it replaced: a verdict written in
# column one, or a marker later inside a status cell, harvested straight through
# into a PUBLIC brain. A missed marker leaks; an over-eager one only costs a row
# that `DROPPED` reports out loud, so this side fails closed.
#
# `(?![-\w])` keeps the REJECT WORD a standalone word, so the compound
# "Reject-first workflow ..." is not treated as a rejection, and "Rejects are
# logged" / "rejecting a plan" stay ordinary prose.
_REJECT_WORD = r"(?:rejected|rejection|reject)"

# A DIRECTIVE is never ordinary prose, so its tail boundary is deliberately
# looser than the reject word's. The separators allow `_` as well as
# space/hyphen (run notes get written in snake_case, and `do_not_harvest:` /
# `private_only` bypassed every rule while reading as the plainest possible
# directive), and a `-`/`_`-joined CONTINUATION is still the directive:
# `do_not_harvest_this_row` slipped past `(?![-\w])` because the trailing `_`
# reads as a word character (audit 2026-07-26). A letter-joined tail is still
# rejected, so "do not harvesting" stays ordinary prose and the standalone-word
# property the earlier rewrites depend on survives.
_DIRECTIVE = r"(?:private[\s\-_]?only|do[\s\-_]?not[\s\-_]?harvest)"
_DIRECTIVE_END = r"(?:(?![-\w])|(?=[-_]\w))"
# The reject word's tail boundary. `(?![-\w])` alone made `-` and `_`
# characters that could NEVER follow a marker, so the glued annotations
# "Rejected--", "Rejected->" and "Rejected_ <reason>" bypassed every rule —
# while the em-dash form "Rejected — ..." was dropped, an arbitrary
# distinction from an author's point of view (adversarial verify
# 2026-07-26). A letter/digit after `-`/`_` still reads as a compound word,
# so "Reject-first workflow ..." stays ordinary prose.
# `_` needs its own carve-out from `\w`: "Rejected_ <reason>" is the marker
# plus an annotation, while "rejected_foo" is a snake_case compound — the
# difference is whether an alphanumeric follows the joiner.
_WORD_END = r"(?![A-Za-z0-9])(?![-_][A-Za-z0-9])"
_MARKER = (r"(?:" + _REJECT_WORD + _WORD_END + r"|"
           + _DIRECTIVE + _DIRECTIVE_END + r")")

# For table cells past the first: marker at the start, anything after it.
REJECT_CELL = re.compile(r"^[\W_]*" + _MARKER, re.I)

# Same marker anywhere inside a status/verdict column, not just at its start:
# "Not approved, rejected" and "No — private-only" are verdicts too.
REJECT_CELL_ANY = re.compile(r"(?<![-\w])" + _MARKER, re.I)

# Directives that are never ordinary prose — honoured anywhere in the text.
REJECT_DIRECTIVE = re.compile(
    r"(?<![-\w])" + _DIRECTIVE + _DIRECTIVE_END, re.I)

# For free prose (a bullet, or a table's lesson cell): require explicit
# annotation punctuation after the marker, so an ordinary sentence like
# "Rejection criteria belong in the rubric" is kept while "Rejected: dupe",
# "Rejected — dupe" and "Rejected, dupe" are dropped. The comma and semicolon
# were missing, so "Rejected, this holds a private token" harvested through.
#
# The class used to be an ENUMERATION of the punctuation seen so far, and an
# enumeration can never be exhaustive: it grew `;,` then `.!?'"` and STILL let
# "Rejected… the transcript holds the client token" (the U+2026 ellipsis that
# smart-punctuation editors substitute for "..."), "Rejected* ...",
# "Rejected → ...", "Rejected = ..." and "Rejected» ..." harvest through, each
# time failing OPEN in the exact family three rewrites had already chased
# (audit 2026-07-26). So define it by what an annotation IS instead: any
# character that is neither a word character nor whitespace. A terminator right
# after the marker also defeats the continuation-word rule below (which needs
# whitespace there), which is why this side has to be closed by construction.
#
# The one carve-out is a POSSESSIVE apostrophe — "Rejection's reason must be
# written down" is ordinary prose, while "'Rejected' pending legal review" is
# an annotation — so an apostrophe counts only when a letter does NOT follow.
# `_` is annotation punctuation too ("Rejected_ holds the token") — but only
# when it does not open a snake_case compound, which is ordinary prose.
_ANNOTATION_PUNCT = r"(?:['’](?![A-Za-z])|[^\w\s'’]|_(?![A-Za-z0-9]))"
REJECT_BULLET = re.compile(
    r"^[\W_]*" + _MARKER + r"[\s*_`]*" + _ANNOTATION_PUNCT, re.I)

# Prose that is NOTHING BUT the marker is a verdict, not a sentence.
REJECT_ONLY = re.compile(r"^[\W_]*" + _MARKER + r"[\W_]*$", re.I)

# A verdict does not always reach for punctuation. "Rejected pending review",
# "Rejected dupe of lesson 12" and "Rejected because it holds a client token"
# are verdicts written in plain words, and the punctuation-only rule above
# silently harvested them. The list must include the causal words too
# ("because/as/for/due/since"), which are the commonest way a reason is
# introduced and the exact miss that motivated this redesign (see the history
# note near the top) — content is often rejected precisely BECAUSE it is
# private, so this direction has to fail closed.
_CONTINUATION = (r"(?:pending|awaiting|by|per|see|note|dupe|duplicate|"
                 r"superseded|stale|obsolete|wontfix|"
                 r"because|as|for|due|since)")
# The marker here is deliberately NARROWER than _MARKER: only the past
# participle and the noun. The bare imperative verb is not a verdict, and
# ordinary safeguard bullets are written that way -- "Reject stale locks before
# retrying", "Reject duplicate submissions at intake", "Reject by default and
# allowlist explicitly" all collide with the continuation words above and were
# being dropped as verdicts (audit 2026-07-26). Verdicts use "Rejected"/
# "Rejection"; nothing is lost, because "private-only" and "do not harvest"
# are still honoured anywhere by REJECT_DIRECTIVE.
_VERDICT_MARKER = r"(?:rejected|rejection)"
# The joiner before the continuation word allows a single `-`/`_` as well as
# whitespace: "Rejected-dupe of lesson 12" is the same verdict as
# "Rejected dupe of lesson 12" with a different separator, and the old
# `(?![-\w])` boundary made it unmatchable (adversarial verify 2026-07-26).
# "Rejection-driven development" stays prose because "driven" is not a
# continuation word.
REJECT_BULLET_CONTINUED = re.compile(
    r"^[\W_]*" + _VERDICT_MARKER + r"(?:(?![-\w])[\s*_`]+|[-_])"
    + _CONTINUATION + r"(?![-\w])", re.I)


def _plain(text):
    """Drop PAIRED markdown emphasis so the annotation rules see the prose.

    Now that any non-word character counts as annotation punctuation, the
    emphasis a lesson is written in must not be mistaken for it: the bullet
    branch already stripped `**bold**` before checking, but a TABLE's lesson
    cell was checked raw, so "| *Rejection* criteria belong in the rubric |"
    would read as "marker followed by punctuation" and be dropped. Only
    BALANCED wrappers are removed, so a lone footnote star ("Rejected* holds
    the client token") is still the annotation it looks like.

    Underscore emphasis is stripped only when the pair sits at WORD
    boundaries: "_do not harvest_" is markdown italics hiding a directive
    (it bypassed every status-column rule while `*do not harvest*` was
    caught — adversarial verify 2026-07-26), while the underscores inside
    `do_not_harvest_this_row` are word-internal and must survive.
    """
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"(?<!\w)__([^_]+)__(?!\w)", r"\1", text)
    text = re.sub(r"(?<!\w)_([^_]+)_(?!\w)", r"\1", text)
    return re.sub(r"`(.+?)`", r"\1", text)


def _is_reject_span(text):
    """The free-prose rule applied to ONE span, read from its start."""
    text = _plain(text)
    return bool(REJECT_DIRECTIVE.search(text)
                or REJECT_BULLET.match(text)
                or REJECT_BULLET_CONTINUED.match(text)
                or REJECT_ONLY.match(text))


def _is_reject_bullet(text):
    """Free-prose rule: bullets and a table row's lesson cell.

    Every rule above reads free prose from its START, so the operator idiom
    that puts the verdict where a reader expects it -- at the END of the line,
    "... [Rejected]", "... (Private-only)", "... -- Rejected" -- walked past
    all of them and harvested with DROPPED EMPTY, i.e. with no "filtered N
    row(s)" line either (audit 2026-07-27). The same trailing annotation was
    ALREADY honoured on a heading and in a status column, so this was an
    internal inconsistency rather than a policy. Free prose carries the same
    annotation slot at its end; see _prose_trailers.
    """
    return bool(_is_reject_span(text)
                or any(_is_reject_span(seg) for seg in _prose_trailers(text)))


def _is_reject_verdict(cell):
    """Status-column rule: a standalone marker anywhere in the cell.

    The cell goes through _plain() first: a status column that literally
    reads "_do not harvest_" or "_Rejected_" is the directive dressed in
    markdown italics, and checking it raw let the underscore wrapper defeat
    the `(?<![-\\w])` word boundary (adversarial verify 2026-07-26).
    """
    return bool(REJECT_CELL_ANY.search(_plain(cell)))


# A bracketed or EMPHASISED trailer is an annotation slot: the wrapper is
# itself the delimiter, so these spans are read from the RAW heading. _plain()
# strips emphasis, so a rule that flattened first could never see that
# "## Lessons **Rejected**" annotates rather than continues the label -- the
# same wrapper-defeats-the-rule shape an earlier verify round added _plain()
# to close, arriving from the other side (judge round 2026-07-26).
_HEADING_BRACKET = re.compile(r"[(\[]([^()\[\]]*)[)\]]")
_HEADING_EMPHASIS = re.compile(
    r"\*\*(.+?)\*\*|\*(.+?)\*|(?<!\w)__([^_]+)__(?!\w)"
    r"|(?<!\w)_([^_]+)_(?!\w)|`(.+?)`")
# Dash separators: an em/en dash needs no spacing (a typographic dash is never
# a word joiner), an ASCII hyphen counts doubled ("Lessons -- Rejected", this
# codebase's own idiom) or with whitespace beside it. A LONE word-internal
# hyphen stays a joiner: splitting on it would manufacture a bare "Rejection"
# segment out of "Rejection-driven development" and re-open the very
# over-exclusion this rule exists to fix.
_HEADING_DASH = re.compile(r"[—–]+|-{2,}|\s-+|-+\s")
# Remaining annotation separators. Deliberately NOT the "any non-word
# character" class the cell rules use: an apostrophe there would split
# "Rejection's reason must be written down" into a bare marker and silently
# drop an ordinary prose section.
_HEADING_SEP = re.compile(r"[:,;|/→⇒]|=>")


def _heading_segments(heading):
    """Every span of a heading that could carry a status annotation."""
    segments = [m.group(1) for m in _HEADING_BRACKET.finditer(heading)]
    for m in _HEADING_EMPHASIS.finditer(heading):
        segments.append(next(g for g in m.groups() if g is not None))
    for part in _HEADING_DASH.split(_plain(heading)):
        segments.extend(_HEADING_SEP.split(part))
    return segments


# Free prose has ONE annotation slot: the TRAILER. Deliberately narrower than
# _heading_segments, which reads EVERY span -- a lesson is a sentence, and
# splitting a sentence on every bracket, colon and comma would manufacture a
# bare "Rejected" out of ordinary prose and silently drop real lessons, the
# exact over-exclusion three earlier rounds of this file opened. A wrapper is
# a trailer only when it CLOSES the text ("... [Rejected]", "... **Rejected**"),
# so a mid-sentence parenthesis stays prose, and the dash tail is the last
# dash-separated part, reusing the heading's dash vocabulary (a lone
# word-internal hyphen is a joiner, never a separator, so
# "Rejection-driven development" is untouched).
_PROSE_TRAILER = re.compile(
    r"(?:[(\[]([^()\[\]]*)[)\]]"
    r"|\*\*([^*]+)\*\*|\*([^*]+)\*"
    r"|(?<!\w)__([^_]+)__|(?<!\w)_([^_]+)_"
    r"|`([^`]+)`)[\W_]*$")


def _prose_trailers(text):
    """The trailing annotation slot(s) of a free-prose lesson."""
    text = text.rstrip()
    segments = []
    # Read the wrapper from the RAW text: _plain() strips the very delimiter
    # that marks the span as an annotation (same reason _heading_segments does).
    wrapped = _PROSE_TRAILER.search(text)
    if wrapped:
        segments.append(next(g for g in wrapped.groups() if g is not None))
    parts = _HEADING_DASH.split(_plain(text))
    if len(parts) > 1:
        segments.append(parts[-1])
    return [s for s in segments if s.strip()]


def _is_reject_heading(heading):
    """Heading rule: a marker counts only as the heading's OPERATIVE LABEL.

    The previous rule ran the permissive status-cell regex (REJECT_CELL_ANY)
    over the WHOLE heading, so a heading that merely MENTIONS the word --
    "## Lessons — why rejected ideas still teach us" -- read as a verdict
    and its entire section silently vanished from the harvest
    (audit 2026-07-26). A heading is a section LABEL plus an optional status
    ANNOTATION, and only the annotation is a status slot: a bracketed or
    emphasised trailer ("## Lessons (private-only)", "## Lessons [REJECTED]",
    "## Lessons **Rejected**") or a tail after a dash / colon / comma
    ("## Lessons — private-only", "## Lessons -- Rejected",
    "## Lessons: do not harvest"). EVERY span gets the same free-prose rule a
    bullet gets -- including the first, because a heading that is nothing but
    a verdict ("## Rejected") has no separator at all and judging only the
    tail let it harvest. So "(rejected — superseded)" and "— Rejected because
    it names a client" are verdicts while "(what rejected drafts teach)",
    "— why rejected ideas still teach us" and "Rejection's reason must be
    written down" stay prose. A DIRECTIVE is still honoured ANYWHERE in the
    line -- "do not harvest" / "private-only" is never ordinary prose on any
    surface -- so that side keeps failing closed.

    The first cut of this rule (same day) split only on a SPACED single dash
    and a colon, and dropped the first span. That re-opened 22 verdict shapes
    HEAD had excluded -- "-- Rejected", the unspaced "—Rejected", emphasised
    "**Rejected**", comma tails, and bare "## Rejected" -- every one silently,
    because an excluded-by-heading section leaves DROPPED empty and never
    prints cmd_scan's "filtered N row(s)" line. Two independent judges caught
    it. That is the fifth time this file has been fixed in one direction while
    its mirror opened: verify BOTH directions differentially against the rule
    you are replacing, never just the case in the bug report.

    The one shape still given up is a verdict glued straight to the label with
    no punctuation, emphasis or separator at all ("## Lessons rejected"); the
    `apply` human gate still reviews whatever such a section stages.
    """
    flat = _plain(heading)
    if REJECT_DIRECTIVE.search(flat):
        return True
    return any(_is_reject_bullet(seg) for seg in _heading_segments(heading)
               if seg.strip())


# Kept as an alias so existing callers/tests that reference REJECT_ROW keep
# working; it is the start-anchored TABLE-cell rule.
REJECT_ROW = REJECT_CELL

# Rows skipped because a cell carried a rejection/private marker. Reported at
# the end of a harvest so the filter is auditable instead of invisible.
DROPPED = []

# Sentinel for a section whose HEADING carries a rejection/private marker.
# Distinct from None (an unrecognised heading, whose rows were never
# candidates): rows under an excluded section were explicitly refused by the
# operator, so each one must land in DROPPED.
_EXCLUDED = object()

# An approval column's explicit refusal. Bare "no" plus the common longhand
# forms; anything that STARTS as a refusal is a refusal even with a reason
# after it ("No — pending legal"). Affirmatives and blanks stay untouched:
# `scan` only stages proposals and `apply` is the human gate, so an empty
# cell means "not decided yet", not "refused".
NOT_APPROVED = re.compile(
    r"^[\W_]*(?:no|not[\s\-_]*approved|not[\s\-_]*for[\s\-_]*reuse|"
    r"denied|declined|never)(?![-\w])", re.I)


def norm(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def brain_norms():
    """Normalized text of every shared-brain entry, for the dedupe comparison.

    A line this cannot parse is a line NOT compared against, so the next scan
    can re-stage a lesson that is already in the brain. That stayed silent
    (`except ValueError: continue`), and a valid-JSON non-object -- `"a string"`
    on its own line -- did not even reach that handler: `.get` raised
    AttributeError and took the whole scan down with a raw traceback
    (audit 2026-07-27). Name what was skipped, on stderr, with line numbers,
    exactly as memory/mneme_adapter.py::_gather does over this same file.
    """
    out, skipped = [], []
    if os.path.isfile(SHARED_BRAIN):
        try:
            with open(SHARED_BRAIN, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except UnicodeDecodeError as exc:
            # No line numbers exist for an undecodable file, so the loss cannot
            # be bounded: dedupe would silently compare against NOTHING, and
            # every candidate row would be staged as fresh. Same refusal
            # sentence the brain's own readers use.
            sys.exit(f"REFUSED: '{SHARED_BRAIN}' is not valid UTF-8 (byte "
                     f"{exc.start}: {exc.reason}); repair the brain file, then "
                     f"re-run — nothing was staged")
        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                skipped.append((line_no, exc))
                continue
            if not isinstance(obj, dict):
                skipped.append((line_no, "not a JSON object"))
                continue
            out.append(norm(obj.get("text", "")))
    for line_no, why in skipped[:10]:
        print(f"harvest: WARNING: shared brain line {line_no} of {SHARED_BRAIN} "
              f"is unreadable ({why}); it was NOT compared against",
              file=sys.stderr)
    if len(skipped) > 10:
        print(f"harvest: WARNING: ... and {len(skipped) - 10} more unreadable "
              f"brain line(s)", file=sys.stderr)
    if skipped:
        print(f"harvest: WARNING: {len(skipped)} brain line(s) took no part in "
              f"the dupe check, so a lesson already in the brain can be staged "
              f"again; repair the brain file", file=sys.stderr)
    return [n for n in out if n]


# Below this many normalized characters a row carries no lesson to speak of.
# A terse operational lesson (for example, "Back up first") is still useful;
# retain it while filtering punctuation and slogan fragments that carry no
# durable guidance.
MIN_LESSON_CHARS = 8


def skip_reason(text, norms):
    """Why this candidate is not staged: "short", "dupe", or None to stage it.

    These are DIFFERENT FACTS and cmd_scan reports them separately. is_dupe()
    returned True for both, so a row with too little content was counted as a
    dupe and `scan` printed "all N already in the brain" -- against a brain
    that can be, and in the repro provably WAS, EMPTY (audit 2026-07-27). That
    is a claim about a lookup that never happened: it tells the operator their
    lesson is safely stored when nothing of the sort is true, and it is the
    reason a content-free row looks identical to a genuine duplicate.
    """
    n = norm(text)
    if len(n) < MIN_LESSON_CHARS:
        return "short"
    # Dupe = the new lesson is contained in an existing entry (or equal).
    # Deliberately NOT bidirectional: a short old entry contained inside a
    # longer new lesson must not silently drop the richer new one.
    if any(n in b for b in norms):
        return "dupe"
    return None


def is_dupe(text, norms):
    """True when the row must not be staged. See skip_reason() for WHY."""
    # Punctuation-only input carries no candidate at all; callers distinguish
    # that from a short but real lesson such as "short".
    return bool(norm(text)) and skip_reason(text, norms) is not None


STATUS_TOKEN = re.compile(
    r"^(?:status\s*[:=]\s*)?"
    r"(reject(?:ed)?|private[-\s]only|do[-\s]not[-\s]harvest"
    r"|approved(?:\s+for\s+reuse)?|yes|no|promote(?:d)?|reuse|keep|pending|draft)"
    r"\s*[.!]?$", re.I)
REJECT_STATUS = re.compile(r"\breject(?:ed)?\b|\bprivate[- ]only\b", re.I)
_STATUS_POSITIONS = (
    r"^\s*(?:status\s*[:=]\s*)?(?P<tok>[^:]{1,32}?)\s*:\s+(?P<rest>\S.*)$",
    r"^(?P<rest>.*?)\s*[(\[]\s*(?P<tok>[^)\]]{1,32}?)\s*[)\]]\s*$",
    r"^(?P<rest>.*\S)\s+(?:--|[|–—-])\s+(?P<tok>[^|–—]{1,32}?)\s*$",
)


def bullet_status(text):
    """Return an explicit bullet status and the lesson text without it."""
    whole = STATUS_TOKEN.match(text.strip())
    if whole:
        return whole.group(1).strip(), ""
    for pattern in _STATUS_POSITIONS:
        found = re.match(pattern, text)
        if found:
            token = found.group("tok").strip()
            if STATUS_TOKEN.match(token):
                return token, found.group("rest").strip()
    return "", text.strip()


def recognized_sections(md):
    """Return headings that map to a durable memory type."""
    found = []
    for line in md.splitlines():
        heading = _HEADING.match(line)
        if heading and _heading_key(line):
            found.append(heading.group(1).strip().lower())
    return found


def _skip_summary(dupes, short):
    """Name each skipped class with its own count, never one as the other."""
    parts = []
    if dupes:
        parts.append(f"{dupes} already in the brain")
    if short:
        parts.append(f"{short} too short / with no harvestable text "
                     f"(under {MIN_LESSON_CHARS} characters)")
    return ", ".join(parts)


# One heading parser, used by BOTH the extractor and the source check in
# cmd_scan. Two copies of "which headings count" would drift, and the drift
# would be exactly the silence this file keeps re-learning: a heading the check
# recognises but the extractor does not (or the reverse) reintroduces the
# stamped-but-unharvested run below.
_HEADING = re.compile(r"^##\s+(.+?)\s*(?:\(.*\))?\s*$")


def _heading_key(line):
    """The shared-brain type a '## ' heading maps to, or None if unrecognised."""
    h = _HEADING.match(line)
    if not h:
        return None
    key = h.group(1).strip().lower()
    return next((v for k, v in SECTION_TYPES.items() if key.startswith(k)), None)


def _recognized_sections(md):
    """True when at least one '## ' heading maps to a SECTION_TYPES key."""
    return bool(recognized_sections(md))


# A bullet or a table row: the two shapes bullets_by_section can harvest.
_ROW = re.compile(r"^\s*[-*]\s+\S|^\s*\|")


def _has_rows(md):
    """True when the file carries content the extractor could have harvested."""
    return any(_ROW.match(line) for line in md.splitlines())


def bullets_by_section(md):
    """Yield (type, text) from ## sections of a 19-memory-harvest.md.

    Accepts BOTH content shapes: bullet lists and markdown tables (the public
    template uses tables; a row is text = first cell). A row is REFUSED —
    recorded in DROPPED instead of yielded — when its lesson cell carries an
    explicit rejection annotation, when a later status cell carries a
    Rejected/Private-only marker, when its approval column says no, or when its
    section heading is annotated. What is NOT refused is a marker glued to
    ordinary prose with no annotation slot at all ("## Lessons rejected"): see
    _is_reject_bullet for why that line is drawn where it is, and `apply` is
    the human gate behind it. The old wording here ("any row marked
    Rejected/Private-only is never harvested") promised more than any rule in
    this file delivers (audit 2026-07-27).
    """
    cur = None
    appr = None
    for line in md.splitlines():
        if _HEADING.match(line):
            cur = _heading_key(line)
            appr = None
            # The regex above STRIPS a trailing parenthetical before the key
            # is matched, so "## Lessons (private-only)" — the least
            # ambiguous marker an operator can write, covering a whole
            # section in one stroke — was silently discarded and every row
            # under it harvested with nothing in DROPPED. The dash/bracket
            # forms ("## Lessons — private-only", "## Lessons [REJECTED]")
            # leaked the same way via the startswith match (adversarial
            # verify 2026-07-26). Only the heading's ANNOTATION is a status
            # slot — running the whole-line verdict rule here discarded
            # sections that merely MENTION rejection ("## Lessons — why
            # rejected ideas still teach us", audit 2026-07-26); see
            # _is_reject_heading. The rows still land in DROPPED below,
            # because a silent section drop is the same defect as a silent
            # row drop.
            if cur and _is_reject_heading(line.lstrip("#").strip()):
                cur = _EXCLUDED
            continue
        if not cur:
            continue
        b = re.match(r"^[-*]\s+(.+)$", line)
        if b:
            if cur is _EXCLUDED:
                DROPPED.append(line.strip()[:120])
                continue
            raw = b.group(1).strip()
            status, remainder = bullet_status(raw)
            if status and (REJECT_STATUS.search(status)
                           or REJECT_DIRECTIVE.search(status)):
                DROPPED.append(line.strip()[:120])
                continue
            if status and remainder:
                raw = remainder
            # The bullet branch used to yield unconditionally while the table
            # branch enforced REJECT_ROW per cell -- a bullet like "Private-only:
            # ..." harvested straight through with no exclusion at all
            # (audit 2026-07-25). Apply the same check here.
            #
            # Check the RAW bullet, exactly as the table branch checks its raw
            # first cell. Flattening `**bold**` FIRST destroyed the delimiter
            # that makes an EMPHASISED trailer an annotation, so
            # "... **Rejected**" survived while the identical table cell was
            # refused (audit 2026-07-27). _is_reject_bullet flattens internally
            # for every start-anchored rule, so nothing is lost by checking raw.
            if _is_reject_bullet(raw):
                DROPPED.append(line.strip()[:120])
                continue
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", raw).strip()
            if text:
                yield cur, text
            continue
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            first = cells[0] if cells else ""
            # Check CELLS, not the raw line: a status cell of "Rejected" skips
            # the row, but the same word inside the lesson text does not.
            if not first or set(first) <= {"-", ":", " "}:  # separator row
                continue
            if first.lower() in ("lesson", "preference", "pattern", "safeguard"):
                # Header row: remember WHICH column is the approval verdict.
                # "Approved For Reuse?" is the template's own reuse gate, and
                # the natural way to decline it is a bare "No" — which no
                # marker enumeration contains, so the operator's answer was
                # discarded with zero signal (adversarial verify 2026-07-26).
                # Identified by header, never by cell shape, so a "No" under
                # an unrelated column ("Uses numpy?") stays ordinary prose.
                #
                # `approv` is tried across ALL headers BEFORE falling back to
                # `reuse`. A single pass with `approv|reuse` locked onto the
                # Project Patterns table's "Reuse Guidance" column (index 2)
                # instead of its "Approved For Reuse?" (index 3), inverting
                # the rule on the shipped template: a row marked "No" was
                # harvested, and an approved row whose guidance began "No more
                # than 8 workers" was dropped. Lessons and User Preferences
                # hid it, because there no earlier column matches
                # (adversarial pre-push audit 2026-07-26).
                appr = next((i for i, c in enumerate(cells)
                             if re.search(r"approv", c, re.I)), None)
                if appr is None:
                    appr = next((i for i, c in enumerate(cells)
                                 if re.search(r"reuse", c, re.I)), None)
                continue
            if cur is _EXCLUDED:
                DROPPED.append(line.strip()[:120])
                continue
            # cells[0] IS the lesson, so it gets the free-prose rule (matching
            # a bare marker there dropped real lessons -- adversarial verify
            # 2026-07-25); cells[1:] are status columns and get the permissive
            # verdict rule. Skipping cells[0] entirely let "| Rejected: holds a
            # private token | ... |" harvest through.
            if (_is_reject_bullet(first)
                    or any(_is_reject_verdict(c) for c in cells[1:])
                    or (appr is not None and appr < len(cells)
                        and NOT_APPROVED.match(cells[appr]))):
                # Record it. Silent filtering is why the old over-broad pattern
                # went unnoticed: a harvest that drops rows must say how many.
                DROPPED.append(line.strip()[:120])
                continue
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", first).strip()
            extra = next((c for c in cells[1:] if c and not _is_reject_verdict(c)), "")
            if text:
                yield cur, (text + (f" — {extra}" if extra else ""))[:400]


def eval_log_candidates(md):
    """Fallback: fail/revise table rows from 12-evaluation-log.md.

    This path used to yield rows with NO reject/private filtering at all: a run
    with no 19-memory-harvest.md staged "| ... token ... | private-only, do not
    harvest |" straight into the proposals file, directive text and all, and
    recorded nothing in DROPPED (audit 2026-07-26). It is the worse half of the
    two paths, because it also joins the status/verdict columns INTO the
    harvested text, so a rejection verdict rides along verbatim. Apply exactly
    the rules bullets_by_section uses: free prose for the first cell, the
    permissive verdict rule for the status columns after it, and the directive
    rule anywhere in the row.
    """
    for line in md.splitlines():
        if not line.strip().startswith("|"):
            continue
        if re.search(r"\bfail(ed)?\b|\brevise[d]?\b|\bwrong\b|\bbug\b", line, re.I):
            cells = [c.strip() for c in line.split("|") if c.strip() and set(c.strip()) != {"-"}]
            if len(cells) >= 2:
                if (REJECT_DIRECTIVE.search(line)
                        or _is_reject_bullet(cells[0])
                        or any(_is_reject_verdict(c) for c in cells[1:])):
                    DROPPED.append(line.strip()[:120])
                    continue
                yield "lesson", " — ".join(cells)[:400]


def _safe_component(value, field="run"):
    if (not isinstance(value, str) or value in (".", "..")
            or not SAFE_COMPONENT.fullmatch(value)):
        raise ValueError(f"{field} must be a safe name inside runs/ (got {value!r})")
    return value


def run_dir(run):
    """Resolve one direct child of runs without following namespace links.

    Searches every configured run root (see _run_roots) but applies the same
    symlink/escape checks per root — multi-root must not mean weaker guards.
    """
    slug = _safe_component(run)
    roots = _run_roots()
    for root_abs in roots:
        if os.path.islink(root_abs):
            raise ValueError(f"unsafe symlinked runs root: {root_abs}")
        root_real = os.path.realpath(root_abs)
        candidate = os.path.join(root_abs, slug)
        if os.path.islink(candidate):
            raise ValueError(f"unsafe symlinked run: {candidate}")
        if os.path.commonpath((root_real, os.path.realpath(candidate))) != root_real:
            raise ValueError(f"run escapes runs root: {run!r}")
        if os.path.isdir(candidate):
            return candidate
    root_abs = roots[0]
    root_real = os.path.realpath(root_abs)
    directory = os.path.join(root_abs, slug)
    if not os.path.isdir(directory):
        raise ValueError(f"no such run: {run}")
    return directory


def _validate_marker_leaf(directory_fd, marker_path):
    """Refuse an existing marker that could alias or block a private write."""
    try:
        marker_stat = os.stat(MARKER, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(marker_stat.st_mode):
        raise ValueError(f"unsafe symlinked harvest marker: {marker_path}")
    if not stat.S_ISREG(marker_stat.st_mode):
        raise ValueError(f"harvest marker is not a regular file: {marker_path}")
    if marker_stat.st_nlink != 1:
        raise ValueError(f"unsafe hard-linked harvest marker: {marker_path}")


def _open_marker_directory(run_path):
    """Open and pin a run directory before inspecting its marker leaf."""
    run_abs = os.path.abspath(run_path)
    marker_path = os.path.join(run_abs, MARKER)
    run_stat = os.lstat(run_abs)
    if stat.S_ISLNK(run_stat.st_mode) or not stat.S_ISDIR(run_stat.st_mode):
        raise ValueError(f"unsafe harvest marker directory: {run_abs}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    directory_fd = os.open(run_abs, flags)
    try:
        opened = os.fstat(directory_fd)
        if (opened.st_dev, opened.st_ino) != (run_stat.st_dev, run_stat.st_ino):
            raise ValueError(f"harvest marker directory changed while opening: {run_abs}")
        _validate_marker_leaf(directory_fd, marker_path)
        return directory_fd
    except Exception:
        os.close(directory_fd)
        raise


def _write_marker(directory_fd, marker_path, value):
    """Atomically replace a marker without following an existing leaf."""
    _validate_marker_leaf(directory_fd, marker_path)
    temporary = None
    try:
        for _ in range(10):
            temporary = f".{MARKER}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                temporary = None
        else:
            raise ValueError("could not allocate a unique harvest marker temporary file")
        with os.fdopen(fd, "w", encoding="utf-8") as marker:
            marker.write(value)
            marker.flush()
            os.fsync(marker.fileno())
        _validate_marker_leaf(directory_fd, marker_path)
        os.replace(temporary, MARKER, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        temporary = None
        os.fsync(directory_fd)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass


def _packets_dir_fd():
    """Open/create the proposal directory without traversing symlinks."""
    packets = os.path.abspath(PACKETS)
    parent, name = os.path.split(packets)
    # Standalone Project OS copies may not have a blackboard yet. Create the
    # known in-project parent first, then still validate every directory entry
    # before publishing a proposal below it.
    os.makedirs(parent, mode=0o755, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    parent_stat = os.lstat(parent)
    if stat.S_ISLNK(parent_stat.st_mode):
        raise ValueError(f"unsafe symlinked proposals parent: {parent}")
    if not stat.S_ISDIR(parent_stat.st_mode):
        raise ValueError(f"proposals parent is not a directory: {parent}")
    parent_fd = os.open(parent, flags)
    try:
        opened_parent = os.fstat(parent_fd)
        if (opened_parent.st_dev, opened_parent.st_ino) != (parent_stat.st_dev, parent_stat.st_ino):
            raise ValueError(f"proposals parent changed while opening: {parent}")
        try:
            os.mkdir(name, 0o755, dir_fd=parent_fd)
        except FileExistsError:
            pass
        packet_stat = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if stat.S_ISLNK(packet_stat.st_mode):
            raise ValueError(f"unsafe symlinked proposals directory: {packets}")
        if not stat.S_ISDIR(packet_stat.st_mode):
            raise ValueError(f"proposals path is not a directory: {packets}")
        packet_fd = os.open(name, flags, dir_fd=parent_fd)
        opened_packets = os.fstat(packet_fd)
        if (opened_packets.st_dev, opened_packets.st_ino) != (packet_stat.st_dev, packet_stat.st_ino):
            os.close(packet_fd)
            raise ValueError(f"proposals directory changed while opening: {packets}")
        return packet_fd
    finally:
        os.close(parent_fd)


def _publish_proposals(filename, rows):
    """Atomically publish new proposals; never replace or follow a leaf."""
    directory_fd = _packets_dir_fd()
    temporary = None
    try:
        for _ in range(10):
            temporary = f".{filename}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
            try:
                fd = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=directory_fd,
                )
                break
            except FileExistsError:
                temporary = None
        else:
            raise ValueError("could not allocate a unique proposal temporary file")
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            for row in rows:
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            output.flush()
            os.fsync(output.fileno())
        try:
            os.link(temporary, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                    follow_symlinks=False)
        except FileExistsError as exc:
            raise ValueError(
                "proposal output already exists; refusing to overwrite: "
                f"{os.path.join(PACKETS, filename)}") from exc
        os.fsync(directory_fd)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
        os.close(directory_fd)


def _secret_reason(candidate):
    """Use the append gate when bundled; retain scan-only portability otherwise."""
    scripts = os.path.join(ROOT, "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    try:
        import brain_append
    except ModuleNotFoundError as exc:
        if exc.name == "brain_append":
            return None
        raise
    return brain_append.secret_reason(candidate)


def read(p):
    """File text; "" when it is absent, None when it exists but cannot be read.

    Every OSError used to collapse into "", so a 19-memory-harvest.md at
    chmod 000 (or any IO fault) was indistinguishable from a run that never had
    one: cmd_scan printed "no harvest sources found", exited 0 and stamped the
    run `.harvested`, taking its lessons off the unharvested queue for good
    while they sat intact on disk (audit 2026-07-27). The caller has to be able
    to tell "there is nothing here" from "I could not look".
    """
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None if os.path.exists(p) else ""


def unharvested():
    out = []
    candidates = []
    for root in _run_roots():
        candidates.extend(glob.glob(os.path.join(root, "*")))
    for d in sorted(candidates):
        if not os.path.isdir(d):
            continue
        done = os.path.isfile(os.path.join(d, "13-delivery-report.md"))
        if done and not os.path.isfile(os.path.join(d, MARKER)):
            out.append(os.path.basename(d))
    return out


def cmd_status():
    u = unharvested()
    print("unharvested done runs: " + (", ".join(u) if u else "none"))
    return 1 if u else 0


def _report_dropped():
    """Print the rejected/private-only audit trail. Called on EVERY scan path.

    This used to live inside the "staged something fresh" branch only, so a
    scan whose rows were ALL filtered printed nothing about the filtering --
    the one case where silent filtering matters most. It is also the
    load-bearing mitigation for the deliberately permissive status-column rule.
    """
    if not DROPPED:
        return
    print(f"  filtered {len(DROPPED)} row(s) marked rejected/private-only:")
    for row in DROPPED[:5]:
        print(f"    - {row}")
    if len(DROPPED) > 5:
        print(f"    ... and {len(DROPPED) - 5} more")


def _refuse(slug, why):
    """Stop without staging and WITHOUT stamping the run harvested.

    `.harvested` is the only thing that takes a finished run off
    `unharvested()`, which feeds `status` and the nightly heartbeat. Stamping a
    run whose source could not actually be harvested is a SILENT loss of the
    discovery signal, so these paths have to exit loud and non-zero instead.
    """
    print(f"{slug}: REFUSED — {why}", file=sys.stderr)
    print("  nothing staged and the run was NOT marked harvested; "
          "fix the source and re-run `scan`.", file=sys.stderr)
    return 2


def cmd_scan(run):
    try:
        d = run_dir(run)
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    slug = os.path.basename(d)
    # DROPPED is module state; a scan reports only its OWN filtered rows.
    del DROPPED[:]
    harvest_md = read(os.path.join(d, "19-memory-harvest.md"))
    if harvest_md is None:
        return _refuse(slug, "19-memory-harvest.md exists but could not be read")
    if harvest_md:
        source_file = "19-memory-harvest.md"
        # Only a '## ' heading that maps to a SECTION_TYPES key is ever read,
        # so a run written in another dialect ("### Lessons", "## Durable
        # lessons (reviewed, safe to promote)" — a shape this repo's own runs/
        # already uses) yielded nothing and was stamped as a completed harvest,
        # taking its lessons off the queue with a message that claimed there
        # were no sources (audit 2026-07-27). Refuse instead — but ONLY when
        # the file actually carries rows: the shipped template ships
        # unrecognised sections beside recognised ones, and a file with no
        # bullet and no table row has nothing to lose, so those stay stamped.
        if not _recognized_sections(harvest_md) and _has_rows(harvest_md):
            return _refuse(
                slug,
                "fallback skipped: 19-memory-harvest.md has rows but no recognized section heading"
                " (recognized: "
                + ", ".join(sorted(SECTION_TYPES)) + ")")
        cands = list(bullets_by_section(harvest_md))
    else:
        # Record the file the lesson actually came from. origin_id was
        # hardcoded to 19-memory-harvest.md, so every fallback proposal
        # misattributed its provenance in a PUBLIC brain (audit 2026-07-26).
        source_file = "12-evaluation-log.md"
        eval_md = read(os.path.join(d, source_file))
        if eval_md is None:
            return _refuse(slug,
                           f"{source_file} exists but could not be read")
        cands = list(eval_log_candidates(eval_md))
    norms = brain_norms()
    today = datetime.date.today().isoformat()
    fresh, skipped_dupe, skipped_short = [], 0, 0
    for i, (typ, text) in enumerate(cands, 1):
        why = skip_reason(text, norms)
        if why == "dupe":
            skipped_dupe += 1
            continue
        if why == "short":
            skipped_short += 1
            continue
        fresh.append({
            "id": f"harvest-{slug}-{today}-{i:02d}", "origin_id": f"{slug}/{source_file}",
            "project_id": slug, "project_name": slug, "source": "harvest.py",
            "tags": [typ, "harvest"], "text": text, "ts": today, "type": typ,
        })
        secret = _secret_reason(fresh[-1])
        if secret:
            print(f"REFUSED: secret/sensitive harvest candidate {i} ({secret})",
                  file=sys.stderr)
            return 2
    if not fresh:
        # "no harvest sources found" was printed even when sources existed and
        # every row was filtered out -- a false report of the one outcome an
        # operator most needs to see (audit 2026-07-26).
        if cands:
            # "all N already in the brain" was printed for rows that were never
            # looked up in the brain at all -- see skip_reason().
            why = (f"all {skipped_dupe + skipped_short} candidate row(s) "
                   f"skipped: {_skip_summary(skipped_dupe, skipped_short)}")
        elif DROPPED:
            why = (f"all {len(DROPPED)} row(s) filtered as "
                   f"rejected/private-only")
        else:
            why = ("no harvest sources found (no 19-memory-harvest.md "
                   "sections, no eval-log hits)")
        print(f"{slug}: {why} — nothing to stage")
        _report_dropped()
        # nothing new is still a completed harvest
        try:
            marker_fd = _open_marker_directory(d)
            try:
                _write_marker(marker_fd, os.path.join(d, MARKER), today + "\n")
            finally:
                os.close(marker_fd)
        except (OSError, ValueError) as exc:
            print(f"REFUSED: could not write harvest marker: {exc}", file=sys.stderr)
            return 2
        return 0
    filename = f"harvest-{slug}-{today}.jsonl"
    out = os.path.join(PACKETS, filename)
    try:
        _publish_proposals(filename, fresh)
    except (OSError, ValueError) as exc:
        print(f"REFUSED: could not stage proposals: {exc}", file=sys.stderr)
        return 2
    total_skipped = skipped_dupe + skipped_short
    note = (f" (skipped {total_skipped}: "
            f"{_skip_summary(skipped_dupe, skipped_short)})") if total_skipped else ""
    print(f"{slug}: staged {len(fresh)} new{note} -> {out}")
    _report_dropped()
    print(f"review the file, then: python3 scripts/harvest.py apply {out}")
    return 0


def _reject_non_finite(value):
    raise ValueError(f"non-finite number {value}")


def _proposal_batch(path):
    """Validate all proposal rows before touching the brain."""
    rows, ids, project_ids = [], set(), set()
    with open(path, encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line, parse_constant=_reject_non_finite)
                if not isinstance(row, dict):
                    raise ValueError(f"expected JSON object, got {type(row).__name__}")
                json.dumps(row, allow_nan=False)
                record_id = row.get("id")
                if not isinstance(record_id, str) or not record_id.strip():
                    raise ValueError("proposal id must be a nonempty string")
                if record_id in ids:
                    raise ValueError(f"duplicate proposal id {record_id!r}")
                ids.add(record_id)
                project_ids.add(_safe_component(row.get("project_id"), "project_id"))
                secret = _secret_reason(row)
                if secret:
                    raise ValueError(secret)
            except (json.JSONDecodeError, ValueError) as exc:
                print(f"REFUSED: invalid proposal line {line_no}: {exc}", file=sys.stderr)
                return None
            rows.append((line_no, line, row))
    if len(project_ids) != 1:
        print("REFUSED: proposals must name exactly one project_id", file=sys.stderr)
        return None
    return rows


def _existing_brain_ids():
    ids = set()
    if not os.path.isfile(SHARED_BRAIN):
        return ids
    with open(SHARED_BRAIN, encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                ids.add(row["id"])
    return ids


def cmd_apply(path):
    if not os.path.isfile(path):
        print(f"no such proposals file: {path}", file=sys.stderr)
        return 2
    proposals = _proposal_batch(path)
    if proposals is None:
        return 2
    slug = proposals[0][2]["project_id"] if proposals else None
    marker_fd = None
    try:
        marker_run = run_dir(slug) if slug else None
        existing_ids = _existing_brain_ids()
        if marker_run:
            marker_fd = _open_marker_directory(marker_run)
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    try:
        appended = kept = 0
        ba = os.path.join(ROOT, "scripts", "brain_append.py")
        for line_no, line, row in proposals:
            if row["id"] in existing_ids:
                kept += 1
                continue
            result = subprocess.run(
                [sys.executable, ba, "--line", line, "--agent", "harvest", "--no-reindex"],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"brain_append failed on line {line_no}: "
                      f"{(result.stderr or result.stdout).strip()[:300]}", file=sys.stderr)
                return 1
            existing_ids.add(row["id"])
            if (result.stdout or "").lstrip().startswith("kept existing id:"):
                kept += 1
            else:
                appended += 1
        rebuild = subprocess.run(
            [sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
            capture_output=True, text=True,
        )
        if rebuild.returncode != 0:
            detail = (rebuild.stderr or rebuild.stdout or "no diagnostic").strip()[:300]
            print(f"reindex failed after append; retry is idempotent: {detail}", file=sys.stderr)
            return 1
        if marker_fd is not None:
            try:
                _write_marker(marker_fd, os.path.join(marker_run, MARKER),
                              datetime.date.today().isoformat() + "\n")
            except (OSError, ValueError) as exc:
                print(f"FAILED: could not write harvest marker: {exc}", file=sys.stderr)
                return 1
        print(f"appended {appended} lessons (kept {kept} existing) + reindexed; "
              f"marked {slug or '?'} harvested")
        return 0
    finally:
        if marker_fd is not None:
            os.close(marker_fd)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("status", "scan", "apply"):
        print(__doc__)
        sys.exit(2)
    if a[0] == "status":
        sys.exit(cmd_status())
    if len(a) < 2:
        sys.exit(f"{a[0]} needs an argument")
    sys.exit(cmd_scan(a[1]) if a[0] == "scan" else cmd_apply(a[1]))
