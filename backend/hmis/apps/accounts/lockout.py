"""
The login lockout, in one place.

Four consecutive failed sign-ins are allowed; the fifth shuts that client out
of that username for five minutes; a success clears the count. That is the
whole rule, and this module is the only thing that knows it — `LoginView` asks
it three questions (is this locked, that one failed, that one worked) and does
nothing else about rate limiting.

**It is not a second authentication system and not a second throttle.**
Authentication is still DRF's `ObtainAuthToken` with `AuthTokenSerializer`;
this only decides whether that serializer is given the chance to run. DRF's own
`SimpleRateThrottle` was the obvious thing to reach for and does not fit: it
counts *requests* in a sliding window, where this counts *consecutive
failures* and has to forget them the moment somebody signs in successfully.
Bending a throttle class into that shape would have meant subverting it.

**What it keeps, and where.** Two short-lived cache entries per (client,
username) — a counter and, once that counter tops out, the moment the lock
lifts. No model: a failed attempt is not something the hospital needs to keep,
and a row that had to be cleaned up later would be a worse answer than a key
that expires by itself. The staff account is never touched, so nothing here can
lock anybody out permanently; the admin's password-reset workflow is untouched
and remains the way back in if somebody genuinely forgets.

**Who gets locked out.** The key is the client's address *and* the username
together. Keying on the username alone would let anyone shut a named member of
staff out of the system from anywhere, just by guessing at their password five
times — a denial of service dressed up as a security control. Keying on the
address alone would shut out a whole department behind one NAT the moment one
person fat-fingered their password. Together, an attacker gets five tries per
username from each address they control, and the nurse at the next desk is
unaffected.

**It reveals nothing.** The count is kept against whatever username was typed,
existing or not, and the refusal is identical either way — so a lockout says
"this client has failed five times", never "this username is real".

**It fails open.** Every cache call is guarded, and a cache that raises or
disappears means sign-in proceeds. A brute-force window is a real cost; a
hospital that cannot reach its own records because a cache is down is a worse
one, and the person locked out by that failure would be the one holding the
patient.
"""
import hashlib
import math
import time

from django.conf import settings
from django.core.cache import cache


def max_attempts():
    """The attempt that locks. Four below it are allowed."""
    return int(getattr(settings, "LOGIN_MAX_FAILED_ATTEMPTS", 5))


def cooldown_seconds():
    return int(getattr(settings, "LOGIN_LOCKOUT_SECONDS", 300))


def client_ip(request):
    """
    The caller's address, trusting `X-Forwarded-For`'s first hop only because
    this application is expected to sit behind the hospital's own proxy. It is
    an identifier for grouping attempts, never an authorisation.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _scope(request, username):
    """
    One opaque key per (client, username).

    Hashed rather than interpolated: a username is a person's, and it has no
    business sitting in plain text in a shared cache — or in whatever dump or
    log line that cache turns up in later. Lower-cased so that `Ada` and `ada`
    are counted as the one person trying, which is what they are.
    """
    raw = f"{client_ip(request)}|{str(username or '').strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _attempts_key(scope):
    return f"login-failures:{scope}"


def _lock_key(scope):
    return f"login-locked-until:{scope}"


def remaining(request, username):
    """
    Seconds left on this client's lockout, or `None` if it may try.

    An absolute unix time is stored rather than leaning on the cache entry's
    own TTL, because reading a TTL back is not something every cache backend
    offers — and the frontend is owed a real figure to count down from.
    """
    scope = _scope(request, username)
    try:
        until = cache.get(_lock_key(scope))
    except Exception:
        return None                      # fail open: see the module docstring
    if not until:
        return None
    # Rounded **up**: the fraction of a second already spent must never shorten
    # what the page tells somebody to wait, or the countdown reaches zero while
    # the server is still refusing and the message becomes a lie.
    left = math.ceil(until - time.time())
    if left > 0:
        return left
    # Expired but not yet evicted; treat it as over.
    try:
        cache.delete(_lock_key(scope))
    except Exception:
        pass
    return None


def record_failure(request, username):
    """
    Count one failed sign-in. Returns the cooldown in seconds if *this* failure
    is the one that locked, otherwise `None`.

    The counter carries the cooldown as its own lifetime, so "consecutive"
    means within one window: four failures this morning and one this afternoon
    are not a brute-force attempt, and treating them as one would lock out the
    person who simply cannot remember which password they chose.
    """
    scope = _scope(request, username)
    key = _attempts_key(scope)
    window = cooldown_seconds()
    try:
        try:
            failures = cache.incr(key)
        except ValueError:
            # No counter yet — `incr` will not create one.
            cache.set(key, 1, window)
            failures = 1
        if failures < max_attempts():
            return None
        cache.set(_lock_key(scope), time.time() + window, window)
        # The count has done its work. Clearing it means the client gets a
        # fresh four attempts when the lock lifts rather than being re-locked
        # by its next single mistake.
        cache.delete(key)
        return window
    except Exception:
        return None                      # fail open


def reset(request, username):
    """Signing in successfully forgets every failure before it."""
    scope = _scope(request, username)
    try:
        cache.delete_many([_attempts_key(scope), _lock_key(scope)])
    except Exception:
        pass
