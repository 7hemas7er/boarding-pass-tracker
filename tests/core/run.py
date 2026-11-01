#!/usr/bin/env python3
"""Runs the core tests (T1, INTEGRATION_PLAN.md 7.1): plain unittest, no Home Assistant, each run in its own process
and its own TMPDIR. A run passes only with exit 0, "OK" and no skipped test.

  python3 -I -B tests/core/run.py                    # once, TZ=Europe/Rome
  python3 -I -B tests/core/run.py --final            # the "final" recipe: TZ=Europe/Rome, then the three time
                                                     # zones and the four fake instants (fakeclock, Europe/Rome)
  python3 -I -B tests/core/run.py --final --cross    # also each fake instant in each of the three zones (3 x 4)
      [--python PATH] [--jobs N] [--tmp DIR] [--json OUT] [-p PATTERN]

The time zones: Pacific/Kiritimati (UTC+14), Etc/GMT+12 (UTC-12), America/New_York. The fake instants
(support/fakeclock.py): 2027-03-28T01:30 (the hour skipped by DST), 2027-10-31T02:30+01:00 (the hour repeated, the
second time), 2027-12-31T23:59:30 (the year changes during the run), 2028-02-29 (a leap day). Prints one line per
run and the totals; exit 0 when every run passed.
"""
import argparse, concurrent.futures, json, os, re, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
FAKECLOCK = os.path.join(HERE, "support", "fakeclock.py")
ZONES = ("Pacific/Kiritimati", "Etc/GMT+12", "America/New_York")
INSTANTS = ("2027-03-28T01:30", "2027-10-31T02:30+01:00", "2027-12-31T23:59:30", "2028-02-29")
BASE_TZ = "Europe/Rome"


def plan(final, cross):
    runs = [("TZ=Europe/Rome", BASE_TZ, None)]
    if final:
        runs += [(f"TZ={z}", z, None) for z in ZONES]
        runs += [(f"fakeclock {w}", BASE_TZ, w) for w in INSTANTS]
    if cross:
        runs += [(f"TZ={z} fakeclock {w}", z, w) for z in ZONES for w in INSTANTS]
    return runs


def run_one(python, name, tz, when, tmp_root, pattern, i):
    tmp = os.path.join(tmp_root, f"run{i:02d}")
    os.makedirs(tmp, exist_ok=True)
    clock = [FAKECLOCK, when] if when else ["-m", "unittest"]
    cmd = [python, "-I", "-B", *clock, "discover", "-s", os.path.relpath(HERE, ROOT), "-p", pattern]
    env = {**os.environ, "TZ": tz, "TMPDIR": tmp}
    env = {k: v for k, v in env.items() if not k.startswith("TRACKER_")}   # the tests set their own
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=3600, stdin=subprocess.DEVNULL)
        rc, out = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out = "timeout", str(e)
    ran = re.findall(r"^Ran (\d+) tests? in", out, re.M)
    last = (out.strip().splitlines() or [""])[-1]
    counts = {k: int(n) for k, n in re.findall(r"(failures|errors|skipped|expected failures)=(\d+)", last)}
    fake = re.findall(r"^\[fakeclock\] now = (.*)$", out, re.M)
    return {"name": name, "tz": tz, "fake_now": fake[0] if fake else None, "exit": rc,
            "ran": int(ran[-1]) if ran else None, "result": last, "failures": counts.get("failures", 0),
            "errors": counts.get("errors", 0), "skipped": counts.get("skipped", 0),
            "secs": round(time.monotonic() - t0, 1), "ok": rc == 0 and bool(ran) and last == "OK",
            "tail": out[-4000:] if not (rc == 0 and last == "OK") else ""}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--final", action="store_true", help="the three time zones and the four fake instants too")
    ap.add_argument("--cross", action="store_true", help="each fake instant in each of the three time zones too")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--jobs", type=int, default=8)
    ap.add_argument("--tmp", help="the folder for the runs' TMPDIRs (default: a new temporary folder)")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("-p", "--pattern", default="test*.py")
    a = ap.parse_args()
    tmp_root = a.tmp or tempfile.mkdtemp(prefix="bpt-core-runs-")
    os.makedirs(tmp_root, exist_ok=True)
    runs = plan(a.final, a.cross)
    with concurrent.futures.ThreadPoolExecutor(max(1, a.jobs)) as ex:
        results = list(ex.map(lambda x: run_one(a.python, *x[1], tmp_root, a.pattern, x[0]), enumerate(runs)))
    for r in results:
        print(f"{'ok  ' if r['ok'] else 'FAIL'} {r['name']:<45} ran {r['ran']}, failures {r['failures']}, errors "
              f"{r['errors']}, skipped {r['skipped']} ({r['secs']} s)" + (f" now={r['fake_now']}" if r["fake_now"] else ""))
        if not r["ok"]:
            print(r["tail"])
    ok = all(r["ok"] for r in results)
    total = {k: sum(r[k] or 0 for r in results) for k in ("ran", "failures", "errors", "skipped")}
    version = subprocess.run([a.python, "-I", "-c", "import sys; print(sys.version.split()[0])"], capture_output=True,
                             text=True).stdout.strip()
    print(f"{'PASS' if ok else 'FAIL'}: python {version}, {len(results)} runs, {total['ran']} tests run, "
          f"{total['failures']} failures, {total['errors']} errors, {total['skipped']} skipped")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"python": version, "runs": results, "total": total, "ok": ok}, f, indent=1)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
