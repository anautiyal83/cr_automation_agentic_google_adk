"""Every run has its own dummy ORDER_NO, TEST_<run id> (user 2026-11-18): a fixed TEST_ORDER_NO met the last run's
backup and response files. Steps 8 and 9 agree within a run; a reused baseline simulator takes this run's number."""
from __future__ import annotations

from core import config


def test_each_run_has_its_own_order_number():
    try:
        config.set_run("223c4607-9486-4c45-81f5-536f98555b43")
        assert config.runtime_params()["ORDER_NO"] == "TEST_223C4607"
        assert config.runtime_params("0f5b826b-6988")["ORDER_NO"] == "TEST_0F5B826B"  # another run, by its id
        assert config.runtime_params()["CHILD_REQ_ID"] == config.DEFAULT_RUNTIME_PARAMS["CHILD_REQ_ID"]
    finally:
        config.set_run("")
    assert config.runtime_params()["ORDER_NO"] == config.DEFAULT_RUNTIME_PARAMS["ORDER_NO"]  # no run: the setting


def test_a_reused_simulator_takes_this_runs_order_number():
    from skills.network_simulator_setup.skill import _this_order
    cfg = "vfs:\n  dirs:\n  - /var/backup/TEST_ORDER_NO\n"
    try:
        config.set_run("aaaabbbb-cccc")
        assert _this_order(cfg, "TEST_ORDER_NO") == "vfs:\n  dirs:\n  - /var/backup/TEST_AAAABBBB\n"
        assert _this_order(cfg, "") == cfg
    finally:
        config.set_run("")
