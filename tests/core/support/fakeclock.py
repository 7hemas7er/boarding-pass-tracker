"""Run a unittest command line with a fake "today" for the whole process.

A copy of the fake clock of the standalone's date checks (the "final" recipe of the core tests: tests/core/run.py).

usage: python3 -I -B fakeclock.py WHEN [unittest args...]
  WHEN = YYYY-MM-DD           -> that day, at the current local time of day
       | YYYY-MM-DDTHH:MM[:SS] -> that local wall-clock instant (then the clock keeps running)
       | ...+HH:MM            -> aware instant

Before anything under test is imported, datetime.date / datetime.datetime in the datetime module are replaced by
subclasses whose today()/now()/utcnow() read a shifted epoch, and time.time()/time.time_ns() are shifted by the
same amount. A metaclass keeps isinstance()/issubclass() true for real date/datetime objects that C code creates
(e.g. datetime.date() of a datetime). sqlite3 adapters are re-registered for the fake classes.
"""
import sys, time, datetime as _dt
import sqlite3       # imported first: sqlite3.dbapi2 binds the real classes; adapters are added for the fakes below

sys.dont_write_bytecode = True

REAL_DATE, REAL_DATETIME, REAL_TIME, REAL_TIME_NS = _dt.date, _dt.datetime, time.time, time.time_ns

when = sys.argv[1]
if "T" in when or " " in when:
    target = REAL_DATETIME.fromisoformat(when)
else:
    target = REAL_DATETIME.combine(REAL_DATE.fromisoformat(when), REAL_DATETIME.now().time())
OFFSET = target.timestamp() - REAL_TIME()      # naive -> local time via mktime, as the code under test would see it


def fake_time():
    return REAL_TIME() + OFFSET


def fake_time_ns():
    return REAL_TIME_NS() + int(OFFSET * 1e9)


class _Meta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, cls._real)

    def __subclasscheck__(cls, sub):
        return issubclass(sub, cls._real)


class FakeDate(REAL_DATE, metaclass=_Meta):
    _real = REAL_DATE

    @classmethod
    def today(cls):
        return cls.fromtimestamp(fake_time())


class FakeDatetime(REAL_DATETIME, metaclass=_Meta):
    _real = REAL_DATETIME

    @classmethod
    def now(cls, tz=None):
        # not cls.fromtimestamp(): for a subclass CPython 3.12 drops fold=1 (the repeated hour when DST ends)
        t = REAL_DATETIME.fromtimestamp(fake_time(), tz)
        return cls(t.year, t.month, t.day, t.hour, t.minute, t.second, t.microsecond, t.tzinfo, fold=t.fold)

    @classmethod
    def today(cls):
        return cls.now()

    @classmethod
    def utcnow(cls):
        return cls.fromtimestamp(fake_time(), _dt.timezone.utc).replace(tzinfo=None)


FakeDate.__name__ = FakeDate.__qualname__ = "date"
FakeDatetime.__name__ = FakeDatetime.__qualname__ = "datetime"
FakeDate.__module__ = FakeDatetime.__module__ = "datetime"

_dt.date, _dt.datetime = FakeDate, FakeDatetime
time.time, time.time_ns = fake_time, fake_time_ns

for real, fake in ((REAL_DATE, FakeDate), (REAL_DATETIME, FakeDatetime)):
    a = sqlite3.adapters.get((real, sqlite3.PrepareProtocol))
    if a:
        sqlite3.register_adapter(fake, a)

# sanity: the patch is effective and consistent
_n = _dt.datetime.now()
assert _dt.date.today() == _n.date(), (_dt.date.today(), _n)
assert abs((_n - target.replace(tzinfo=None) if target.tzinfo is None else
            _dt.datetime.now(_dt.timezone.utc) - target).total_seconds()) < 5
assert isinstance(REAL_DATE(2020, 1, 1), _dt.date) and isinstance(_n, _dt.date)
print(f"[fakeclock] now = {_dt.datetime.now().astimezone().isoformat(timespec='seconds')} fold={_dt.datetime.now().fold}", file=sys.stderr, flush=True)

import unittest
unittest.main(module=None, argv=["fakeclock"] + sys.argv[2:])
