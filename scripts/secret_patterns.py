#!/usr/bin/env python3
"""secret_patterns — the single credential denylist every memory gate uses.

Before this module the same list existed in four hand-maintained copies
(scripts/brain_append.py, scripts/import_chat_history.py,
addons/full-engine/brain/brain.py, addons/full-engine/brain/central_brain.py,
addons/full-engine/memory/osvec_adapter.py) and they had already drifted four
ways: different Slack thresholds, different sensitive-key sets, only one copy
scanning dict KEYS, and the redactor missing sk_live_/xox*/glpat- entirely. A
privacy gate that silently drifts below its siblings is worse than no gate,
because it still prints a passing checkbox.

This file lives in scripts/ on purpose: setup_project_os.py copies scripts/ in
EVERY install, core or full-engine, so add-on modules can always import it.
Importers must fail loudly if it is missing (see _load_secret_patterns in the
add-on files) — never fall back to a weaker local copy.

Two lists are exported and they are not interchangeable:

* SECRET_PATTERNS — credentials. Matching one is grounds for REFUSING a write.
* PII_REDACTION_SPECS — emails/SSN/phone shapes. Redact these on the way into a
  private report, but never refuse a memory record for containing an email.
"""
import re

# (regex source, redaction label). The label is what import_chat_history writes
# in place of the match; the compiled form is what the memory gates search for.
SECRET_PATTERN_SPECS = (
    (r"(?<![A-Za-z0-9_])sk-[A-Za-z0-9_\-]{16,}", "[REDACTED_API_KEY]"),
    (r"(?<![A-Za-z0-9_])sk_(live|test)_[A-Za-z0-9]{16,}", "[REDACTED_STRIPE_KEY]"),
    (r"(?<![A-Za-z0-9_])rk_(live|test)_[A-Za-z0-9]{16,}", "[REDACTED_STRIPE_KEY]"),
    (r"AKIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    (r"ASIA[0-9A-Z]{16}", "[REDACTED_AWS_KEY]"),
    (r"(?<![A-Za-z0-9_])gh[pousr]_[A-Za-z0-9]{20,}", "[REDACTED_GITHUB_TOKEN]"),
    (r"github_pat_[A-Za-z0-9_]{20,}", "[REDACTED_GITHUB_TOKEN]"),
    (r"(?<![A-Za-z0-9_])AIza[0-9A-Za-z_\-]{20,}", "[REDACTED_GOOGLE_KEY]"),
    (r"(?<![A-Za-z0-9_])(?:ya29\.[A-Za-z0-9_\-]{20,}|GOCSPX-[A-Za-z0-9_\-]{20,})", "[REDACTED_GOOGLE_OAUTH_SECRET]"),
    (r"(?<![A-Za-z0-9_])xox[baprs]-[A-Za-z0-9\-]{10,}", "[REDACTED_SLACK_TOKEN]"),
    (r"(?<![A-Za-z0-9_])figd_[A-Za-z0-9_\-]{20,}", "[REDACTED_FIGMA_TOKEN]"),
    (r"SG\.[A-Za-z0-9_\-]{20,}", "[REDACTED_SENDGRID_TOKEN]"),
    (r"\bAC[0-9a-fA-F]{32}\b", "[REDACTED_TWILIO_SID]"),
    (r"\bSK[0-9a-fA-F]{32}\b", "[REDACTED_TWILIO_KEY]"),
    (r"(?<![A-Za-z0-9_])glpat-[A-Za-z0-9_\-]{16,}", "[REDACTED_GITLAB_TOKEN]"),
    (r"(?<![A-Za-z0-9_])dop_v1_[A-Za-z0-9]{32,}", "[REDACTED_DIGITALOCEAN_TOKEN]"),
    (r"(?<![A-Za-z0-9_])npm_[A-Za-z0-9]{30,}", "[REDACTED_NPM_TOKEN]"),
    (r"(?<![A-Za-z0-9_])hf_[A-Za-z0-9]{30,}", "[REDACTED_HUGGINGFACE_TOKEN]"),
    (r"(?<![A-Za-z0-9_])ntn_[A-Za-z0-9]{40,}", "[REDACTED_NOTION_TOKEN]"),
    (r"(?<![A-Za-z0-9_])lin_api_[A-Za-z0-9]{30,}", "[REDACTED_LINEAR_TOKEN]"),
    (r"(?<![A-Za-z0-9_])vercel_[A-Za-z0-9]{20,}", "[REDACTED_VERCEL_TOKEN]"),
    (r"(?i)https://[0-9a-f]{32}@[\w.\-]+/\d+", "[REDACTED_SENTRY_DSN]"),
    (r"(?i)AccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}", "[REDACTED_AZURE_STORAGE_KEY]"),
    (r"https://hooks\.slack\.com/services/T[A-Za-z0-9/]{20,}", "[REDACTED_SLACK_WEBHOOK]"),
    (r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.", "[REDACTED_JWT]"),
    (r"(?i)\b(postgres(ql)?|mysql|mongodb(\+srv)?|redis|amqp)://[^\s:@/]+:[^\s@/]+@", "[REDACTED_URL_CREDENTIAL]"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "[REDACTED_PRIVATE_KEY]"),
    (r"(?i)(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S{6,}|(?:authorization\s*[:=]\s*)?bearer\s+(?!token\b)[A-Za-z0-9._~+/=-]{8,}", "[REDACTED_SECRET]"),
)

SECRET_PATTERNS = tuple(re.compile(source) for source, _ in SECRET_PATTERN_SPECS)

# Personal data. Redacted in private reports; NOT grounds for refusing a write.
PII_REDACTION_SPECS = (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
     "[REDACTED_PRIVATE_KEY]", re.S),
    (r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[REDACTED_EMAIL]", 0),
    (r"\b\d{3}-\d{2}-\d{4}\b", "[REDACTED_SSN_LIKE_VALUE]", 0),
    (r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b",
     "[REDACTED_PHONE_LIKE_VALUE]", 0),
)

SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "secret", "password", "passwd", "token",
    "authorization", "access_token", "refresh_token", "auth_token",
    "client_secret", "private_key",
})
SENSITIVE_KEY_SUFFIXES = (
    "_api_key", "_secret", "_password", "_passwd", "_token",
    "_authorization", "_private_key",
)


def looks_like_secret(text):
    """Return the matching pattern source, or None. Truthy means REFUSE."""
    if not isinstance(text, str):
        return None
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return pattern.pattern
    return None


def is_sensitive_key(key):
    """True when a mapping key names a credential field (api_key, db_password)."""
    normalized = re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")
    return (normalized in SENSITIVE_KEYS
            or any(normalized.endswith(s) for s in SENSITIVE_KEY_SUFFIXES))


def secret_reason(value, path="record", _seen=None):
    """Return a human-readable reason to refuse `value`, or None to allow it.

    Walks strings, mappings and sequences. Mapping KEYS are scanned too — a
    record shaped {"sk_live_...": "note"} hides the credential in the key, and
    for years only one of the five copies of this walk checked for that.
    """
    if _seen is None:
        _seen = set()
    if isinstance(value, dict):
        marker = id(value)
        if marker in _seen:
            return None
        _seen.add(marker)
        for key, child in value.items():
            if is_sensitive_key(key) and child not in (None, "", False):
                return "sensitive field %s.%s" % (path, key)
            if looks_like_secret(str(key)):
                return "secret-looking key at %s.%s" % (path, key)
            if isinstance(key, str):
                if isinstance(child, str):
                    joined = child
                elif isinstance(child, (bytes, bytearray)):
                    joined = child.decode("utf-8", "replace")
                else:
                    joined = None
                if joined is not None:
                    # A secret may be split across a JSON key and its value:
                    # `sk-ant-api03` plus the remaining token characters is
                    # individually clean but unsafe once reassembled.
                    if looks_like_secret(key + joined):
                        return "split credential at %s.%s" % (path, key)
                    if (joined.strip() and not re.search(r"\s", joined)
                            and joined.strip().upper() not in {"REDACTED", "***"}
                            and looks_like_secret(key + "=" + joined)):
                        return "split credential at %s.%s" % (path, key)
            found = secret_reason(child, "%s.%s" % (path, key), _seen)
            if found:
                return found
        return None
    if isinstance(value, (list, tuple)):
        marker = id(value)
        if marker in _seen:
            return None
        _seen.add(marker)
        for index, child in enumerate(value):
            found = secret_reason(child, "%s[%s]" % (path, index), _seen)
            if found:
                return found
        return None
    if isinstance(value, str) and looks_like_secret(value):
        return "secret-looking text at %s" % path
    return None


def redaction_pairs():
    """(compiled pattern, label) pairs for report redaction: secrets then PII."""
    pairs = [(re.compile(source), label)
             for source, label in SECRET_PATTERN_SPECS]
    pairs += [(re.compile(source, flags), label)
              for source, label, flags in PII_REDACTION_SPECS]
    return pairs
