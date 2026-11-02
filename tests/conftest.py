"""Tests never read or write the real command sample library (data/knowledge)."""
import os
import tempfile

os.environ["NETCONFIG_SAMPLE_LIBRARY"] = os.path.join(tempfile.mkdtemp(prefix="netconfig-tests-"),
                                                      "command_samples.json")
