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
import os, re, sys, json, glob, datetime, subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project-os/
HOME = os.path.expanduser("~")
SHARED_BRAIN = os.environ.get(
    "PROJECT_OS_SHARED_BRAIN",
    os.path.join(HOME, ".project-os", "central-brain", "shared-brain.jsonl"))
RUNS = os.path.join(ROOT, "runs")
PACKETS = os.path.join(ROOT, "blackboard", "packets")
MARKER = ".harvested"

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


def _is_reject_bullet(text):
    """Free-prose rule: bullets and a table row's lesson cell."""
    text = _plain(text)
    return bool(REJECT_DIRECTIVE.search(text)
                or REJECT_BULLET.match(text)
                or REJECT_BULLET_CONTINUED.match(text)
                or REJECT_ONLY.match(text))


def _is_reject_verdict(cell):
    """Status-column rule: a standalone marker anywhere in the cell.

    The cell goes through _plain() first: a status column that literally
    reads "_do not harvest_" or "_Rejected_" is the directive dressed in
    markdown italics, and checking it raw let the underscore wrapper defeat
    the `(?<![-\\w])` word boundary (adversarial verify 2026-07-26).
    """
    return bool(REJECT_CELL_ANY.search(_plain(cell)))


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
    out = []
    if os.path.isfile(SHARED_BRAIN):
        with open(SHARED_BRAIN, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(norm(json.loads(line).get("text", "")))
                except ValueError:
                    continue
    return [n for n in out if n]


def is_dupe(text, norms):
    n = norm(text)
    if len(n) < 20:
        return True  # too short to be a lesson; treat as noise
    # Dupe = the new lesson is contained in an existing entry (or equal).
    # Deliberately NOT bidirectional: a short old entry contained inside a
    # longer new lesson must not silently drop the richer new one.
    return any(n in b for b in norms)


def bullets_by_section(md):
    """Yield (type, text) from ## sections of a 19-memory-harvest.md.
    Accepts BOTH content shapes: bullet lists and markdown tables (the public
    template uses tables; a row is text = first cell, and any row marked
    Rejected/Private-only is never harvested)."""
    cur = None
    appr = None
    for line in md.splitlines():
        h = re.match(r"^##\s+(.+?)\s*(?:\(.*\))?\s*$", line)
        if h:
            key = h.group(1).strip().lower()
            cur = next((v for k, v in SECTION_TYPES.items() if key.startswith(k)), None)
            appr = None
            # The regex above STRIPS a trailing parenthetical before the key
            # is matched, so "## Lessons (private-only)" — the least
            # ambiguous marker an operator can write, covering a whole
            # section in one stroke — was silently discarded and every row
            # under it harvested with nothing in DROPPED. The dash/bracket
            # forms ("## Lessons — private-only", "## Lessons [REJECTED]")
            # leaked the same way via the startswith match (adversarial
            # verify 2026-07-26). A heading is a status slot, not free
            # prose, so it gets the permissive verdict rule over the WHOLE
            # line; the rows still land in DROPPED below, because a silent
            # section drop is the same defect as a silent row drop.
            if cur and REJECT_CELL_ANY.search(_plain(line.lstrip("#").strip())):
                cur = _EXCLUDED
            continue
        if not cur:
            continue
        b = re.match(r"^[-*]\s+(.+)$", line)
        if b:
            if cur is _EXCLUDED:
                DROPPED.append(line.strip()[:120])
                continue
            text = re.sub(r"\*\*(.+?)\*\*", r"\1", b.group(1)).strip()
            # The bullet branch used to yield unconditionally while the table
            # branch enforced REJECT_ROW per cell -- a bullet like "Private-only:
            # ..." harvested straight through with no exclusion at all
            # (audit 2026-07-25). Apply the same check here.
            if _is_reject_bullet(text):
                DROPPED.append(line.strip()[:120])
                continue
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


def run_dir(run):
    d = run if os.path.isdir(run) else os.path.join(RUNS, run)
    if not os.path.isdir(d):
        sys.exit(f"no such run: {run}")
    return d


def read(p):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def unharvested():
    out = []
    for d in sorted(glob.glob(os.path.join(RUNS, "*"))):
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


def cmd_scan(run):
    d = run_dir(run)
    slug = os.path.basename(d)
    # DROPPED is module state; a scan reports only its OWN filtered rows.
    del DROPPED[:]
    harvest_md = read(os.path.join(d, "19-memory-harvest.md"))
    if harvest_md:
        source_file = "19-memory-harvest.md"
        cands = list(bullets_by_section(harvest_md))
    else:
        # Record the file the lesson actually came from. origin_id was
        # hardcoded to 19-memory-harvest.md, so every fallback proposal
        # misattributed its provenance in a PUBLIC brain (audit 2026-07-26).
        source_file = "12-evaluation-log.md"
        cands = list(eval_log_candidates(read(os.path.join(d, source_file))))
    norms = brain_norms()
    today = datetime.date.today().isoformat()
    fresh, skipped = [], 0
    for i, (typ, text) in enumerate(cands, 1):
        if is_dupe(text, norms):
            skipped += 1
            continue
        fresh.append({
            "id": f"harvest-{slug}-{today}-{i:02d}", "origin_id": f"{slug}/{source_file}",
            "project_id": slug, "project_name": slug, "source": "harvest.py",
            "tags": [typ, "harvest"], "text": text, "ts": today, "type": typ,
        })
    if not fresh:
        # "no harvest sources found" was printed even when sources existed and
        # every row was filtered out -- a false report of the one outcome an
        # operator most needs to see (audit 2026-07-26).
        if cands:
            why = f"all {skipped} already in the brain"
        elif DROPPED:
            why = (f"all {len(DROPPED)} row(s) filtered as "
                   f"rejected/private-only")
        else:
            why = ("no harvest sources found (no 19-memory-harvest.md "
                   "sections, no eval-log hits)")
        print(f"{slug}: {why} — nothing to stage")
        _report_dropped()
        # nothing new is still a completed harvest
        open(os.path.join(d, MARKER), "w", encoding="utf-8").write(today + "\n")
        return 0
    os.makedirs(PACKETS, exist_ok=True)
    out = os.path.join(PACKETS, f"harvest-{slug}-{today}.jsonl")
    with open(out, "w", encoding="utf-8") as f:
        for o in fresh:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    print(f"{slug}: staged {len(fresh)} new (skipped {skipped} dupes) -> {out}")
    _report_dropped()
    print(f"review the file, then: python3 scripts/harvest.py apply {out}")
    return 0


def cmd_apply(path):
    if not os.path.isfile(path):
        sys.exit(f"no such proposals file: {path}")
    ba = os.path.join(ROOT, "scripts", "brain_append.py")
    # Parse EVERY line before appending any of them. The old loop parsed and
    # appended in the same pass, so a malformed line N left lines 1..N-1
    # already committed to the brain despite the docstring's promise to
    # "refuse malformed before touching the brain" (audit 2026-07-25).
    lines, slugs = [], []
    with open(path, encoding="utf-8") as f:
        for i, raw in enumerate(f, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except ValueError as e:
                sys.exit(f"malformed proposals line {i}, nothing appended: {e}")
            lines.append(line)
            slugs.append(o.get("project_id"))
    n = 0
    for line in lines:
        r = subprocess.run([sys.executable, ba, "--line", line, "--agent", "harvest",
                            "--no-reindex"], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"brain_append failed on line {n + 1}: "
                     f"{(r.stderr or r.stdout).strip()[:300]}")
        n += 1
    # one reindex for the whole batch
    subprocess.run([sys.executable, os.path.join(ROOT, "memory", "mneme_adapter.py"), "build"],
                   check=True)
    # Mark EVERY contributing run harvested, not just the last project_id seen:
    # a batch spanning proja/projb/projc used to leave proja and projb
    # unmarked even though their lessons were appended (audit 2026-07-25).
    marked = []
    for slug in dict.fromkeys(s for s in slugs if s):
        if os.path.isdir(os.path.join(RUNS, slug)):
            with open(os.path.join(RUNS, slug, MARKER), "w", encoding="utf-8") as f:
                f.write(datetime.date.today().isoformat() + "\n")
            marked.append(slug)
    print(f"appended {n} lessons + reindexed; marked {', '.join(marked) if marked else '?'} harvested")
    return 0


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
