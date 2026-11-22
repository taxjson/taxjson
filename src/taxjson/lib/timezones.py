"""The local time zone crypto rows are dated in: the zone named by
[settings] local_timezone (TAXJSON_LOCAL_TZ in a tool's environment),
the message when none is, a misspelled key's note, this machine's zone
(system_timezone, valid_timezone: the suggestion, and what `tjs init`
writes as local_timezone), and the UTC stamp read in that zone
(utc_to_local, with a fall-back hour's two readings kept in order).
Out of lib/brokerages/_crypto_common and lib/config_template, which
imported each other for these (step 8 of the architecture plan: lib's
import cycle).

Kraken and Coinbase stamp every row in UTC. A Canadian taxpayer's
trade/income DATE is the local calendar date, so a fill at 2026-01-01
03:00 UTC belongs to 2025-12-31 in Toronto (and to the 2025 return).
Rows are converted to the user's local time: the IANA zone in the
`TAXJSON_LOCAL_TZ` environment variable, which `taxjson` sets from the
project's [settings] local_timezone. There is no default zone: a crypto
row with none named is refused (LocalTimezoneMissing) rather than dated
in someone else's time."""

import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from taxjson.project.settings import as_settings


ENV_LOCAL_TZ = 'TAXJSON_LOCAL_TZ'


class LocalTimezoneMissing(ValueError):
    """No local time zone is named for crypto UTC stamps."""


def missing_timezone_message(where: str = "project") -> str:
    """The one message for a crypto row (or a crypto project) with no
    local time zone named: the key to set and this machine's zone as a
    suggestion when it can be read (not UTC)."""
    try:
        sys_tz = system_timezone()
    except Exception:   # noqa: BLE001 — a suggestion only
        sys_tz = None
    if sys_tz:
        hint = (f"this machine's zone is {sys_tz} — if that is where you "
                f"live, set local_timezone = \"{sys_tz}\"")
    else:
        hint = ("this machine's zone cannot be read or is UTC (a server "
                "default), so name yours: an IANA zone such as "
                "\"America/Vancouver\" or \"Europe/London\"")
    lead = ("crypto exchanges stamp every row in UTC and taxjson dates "
            "it in YOUR local time zone, which has no default")
    if where == "project":
        return (f"{lead}: add local_timezone to [settings] in taxjson.toml "
                f"({hint}).")
    return (f"{lead}: set the {ENV_LOCAL_TZ} environment variable to an "
            f"IANA zone name, or run inside a project with [settings] "
            f"local_timezone ({hint}).")


def timezone_typo(settings) -> Optional[str]:
    """The [settings] key that looks like a misspelled local_timezone
    (`local_timezoen`, `Local_Timezone`), else None: the run's and the
    checklist's 'has no local_timezone' name it, as the unknown-key
    warning they stop before would have (backlog TJ-0034)."""
    import difflib
    keys = {str(k).lower(): str(k) for k in as_settings(settings).present
            if k != "local_timezone"}
    close = difflib.get_close_matches("local_timezone", list(keys), n=1,
                                      cutoff=0.8)
    return keys[close[0]] if close else None


def timezone_typo_note(settings) -> str:
    """` (it has 'local_timezoen': is that local_timezone misspelled?)`,
    or "" when no [settings] key looks like one."""
    typo = timezone_typo(settings)
    return (f" (it has {typo!r}: is that local_timezone misspelled?)"
            if typo else "")


def local_tz_name() -> str:
    """The zone crypto rows are dated in (TAXJSON_LOCAL_TZ; `taxjson`
    sets it from [settings] local_timezone). LocalTimezoneMissing when
    none is named."""
    name = (os.environ.get(ENV_LOCAL_TZ) or '').strip()
    if not name:
        raise LocalTimezoneMissing(missing_timezone_message("env"))
    return name


def _eastern_offset_hours(dt_utc: datetime) -> int:
    """UTC offset for North-American Eastern time from the DST rules —
    the fallback when the platform has no tz database (Windows without
    the `tzdata` package). 2007+: second Sunday of March 07:00 UTC to
    first Sunday of November 06:00 UTC; before: first Sunday of April
    to last Sunday of October."""
    y = dt_utc.year

    def nth_sunday(month, n):
        d = datetime(y, month, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        return d + timedelta(weeks=n - 1)

    if y >= 2007:
        start = nth_sunday(3, 2) + timedelta(hours=7)
        end = nth_sunday(11, 1) + timedelta(hours=6)
    else:
        start = nth_sunday(4, 1) + timedelta(hours=7)
        last_oct = datetime(y, 10, 31)
        last_oct -= timedelta(days=(last_oct.weekday() + 1) % 7)
        end = last_oct + timedelta(hours=6)
    return -4 if start <= dt_utc < end else -5


def _halved(w0: datetime, elapsed: timedelta) -> datetime:
    """`w0` plus half of `elapsed`, in whole seconds."""
    return w0 + timedelta(seconds=int(elapsed.total_seconds()) // 2)


def _eastern_local(dt_utc: datetime) -> datetime:
    """utc_to_local for North-American Eastern time without a tz
    database (_eastern_offset_hours), the repeated hour ordered as
    utc_to_local orders it."""
    off = _eastern_offset_hours(dt_utc)
    local = dt_utc + timedelta(hours=off)
    if dt_utc.year >= 2007:
        d = datetime(dt_utc.year, 11, 1)
        d += timedelta(days=(6 - d.weekday()) % 7)
        end = d + timedelta(hours=6)
    else:
        last_oct = datetime(dt_utc.year, 10, 31)
        last_oct -= timedelta(days=(last_oct.weekday() + 1) % 7)
        end = last_oct + timedelta(hours=6)
    start = end - timedelta(hours=1)
    if start <= dt_utc < end + timedelta(hours=1):
        return _halved(start - timedelta(hours=4), dt_utc - start)
    return local


def utc_to_local(dt_utc: datetime, tz_name: Optional[str] = None) -> datetime:
    """Naive UTC datetime -> naive local wall-clock datetime.

    In the hour the clocks repeat when they fall back (01:00-01:59 twice
    in Toronto), the wall clock runs backwards: a sale at 05:50 UTC
    (01:50 EDT) and a rebuy at 06:10 UTC (01:10 EST) read in that order
    by their local times put the rebuy first, and the canonical replay
    (date, time) booked them so (backlog TJ-0747). Inside that hour the
    two passes are compressed into it in order — the first pass into
    its first half, the second into its second half, each instant at
    half its distance from the hour's start — so local time order is
    the instants' order, except that two instants one second apart may
    share a whole second (the books store whole seconds: a parser keeps
    those rows in order, order_within_seconds; backlog TJ-1056); the date
    is the local date as before."""
    name = tz_name or local_tz_name()
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(name)
    except Exception:
        if name in ('America/Toronto', 'America/Montreal',
                    'America/New_York', 'US/Eastern', 'Canada/Eastern'):
            return _eastern_local(dt_utc)
        raise ValueError(
            f"local time zone {name!r} (TAXJSON_LOCAL_TZ) is not available "
            f"— install the `tzdata` package or use an IANA zone name "
            f"such as America/Toronto.")
    local = (dt_utc.replace(tzinfo=timezone.utc).astimezone(tz)
             .replace(tzinfo=None))
    return _fold_ordered(dt_utc, local, tz)


def _ambiguous(w: datetime, tz) -> bool:
    """Wall time `w` occurs twice in `tz` (the repeated hour)."""
    a = w.replace(tzinfo=tz, fold=0).utcoffset()
    b = w.replace(tzinfo=tz, fold=1).utcoffset()
    return a is not None and b is not None and a > b


def _fold_ordered(dt_utc: datetime, local: datetime, tz) -> datetime:
    """`local` (the wall time of `dt_utc` in `tz`), or, inside a
    repeated hour, its order-keeping stand-in (utc_to_local)."""
    if not _ambiguous(local, tz):
        return local
    first = local.replace(tzinfo=tz, fold=0).utcoffset()
    second = local.replace(tzinfo=tz, fold=1).utcoffset()
    span = first - second               # how long the hour repeats
    on_second = dt_utc + second == local
    # The repeated span's first wall time: step back while ambiguous.
    w0 = local
    for step in (timedelta(minutes=1), timedelta(seconds=1)):
        while _ambiguous(w0 - step, tz) and local - (w0 - step) < span:
            w0 -= step
    elapsed = (local - w0) + (span if on_second else timedelta(0))
    return _halved(w0, elapsed)


def system_timezone() -> Optional[str]:
    """This machine's IANA zone name (TZ, /etc/timezone, the
    /etc/localtime link), or None when none can be read or it is not a
    zone name taxjson accepts."""
    cands: List[str] = []
    env = (os.environ.get("TZ") or "").strip().lstrip(":")
    if env:
        cands.append(env)
    try:
        with open("/etc/timezone", encoding="utf-8") as f:
            cands.append(f.readline().strip())
    except OSError:
        pass
    try:
        link = os.readlink("/etc/localtime")
        if "zoneinfo/" in link:
            cands.append(link.split("zoneinfo/", 1)[1])
    except OSError:
        pass
    # Servers, containers and WSL run on UTC whatever the user's zone
    # is: a UTC zone never names where the user lives. TZ, when set, is
    # the user's word and decides (UTC there: the key stays commented);
    # among the files, a stale /etc/timezone saying Etc/UTC beside an
    # /etc/localtime link to America/Toronto (2026-10 workflow audit)
    # does not hide the link — the first real zone wins, and only UTC
    # everywhere leaves the key commented.
    if env:
        return env if env not in _SERVER_DEFAULT_ZONES \
            and valid_timezone(env) else None
    for name in cands:
        if name in _SERVER_DEFAULT_ZONES:
            continue
        if valid_timezone(name):
            return name
    return None


_SERVER_DEFAULT_ZONES = frozenset({
    "UTC", "Etc/UTC", "Etc/UCT", "UCT", "Etc/Universal", "Universal",
    "Etc/Zulu", "Zulu", "GMT", "Etc/GMT", "Etc/GMT0", "Etc/GMT+0",
    "Etc/GMT-0", "Etc/Greenwich", "Greenwich", "GMT0"})


def valid_timezone(name: str) -> bool:
    """An IANA Area/Location name (or UTC) the crypto dating accepts."""
    if not name or ("/" not in name and name != "UTC") \
            or name.startswith(("/", "posix/", "right/")):
        return False
    try:
        utc_to_local(datetime(2025, 1, 1), name)
    except Exception:  # noqa: BLE001 — any failure: not a usable zone
        return False
    return True
