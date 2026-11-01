"""The core of the boarding_pass_tracker integration: the standalone service's engine, ported (INTEGRATION_PLAN.md
3.7). No module of this package imports Home Assistant (tests/core/test_layering.py), and every function here runs
in Home Assistant's executor, never on its event loop."""
