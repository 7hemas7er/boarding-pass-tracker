"""Support of the core tests (tests/core): the ported core on sys.path, and its log lines printed to the current
sys.stdout with the standalone's timestamp, as its log() printed them, so the copied tests that read the captured
output (privacy checks, request lines) keep working (INTEGRATION_PLAN.md 7.2, hunk 2).

  ROOT the repository, CORE custom_components/boarding_pass_tracker/core, TESTS tests/core.
"""
import datetime, logging, os, sys

TESTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(TESTS))
CORE = os.path.join(ROOT, "custom_components", "boarding_pass_tracker", "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

# the loggers of the core's modules, imported top-level here (in Home Assistant: custom_components.<...>.core.<name>)
LOGGERS = ("engine", "ha", "api", "barcodes")


class CurrentStdout(logging.Handler):
    """Writes "YYYY-mm-dd HH:MM:SS <message>" to sys.stdout as it is at each record (a redirect_stdout of a test
    captures it), like the standalone's log()."""

    def emit(self, record):
        try:
            sys.stdout.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S} {record.getMessage()}\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)


HANDLER = CurrentStdout()
for _name in LOGGERS:
    _logger = logging.getLogger(_name)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False
    if not any(isinstance(h, CurrentStdout) for h in _logger.handlers):
        _logger.addHandler(HANDLER)
