"""One portable test entry point; subprocesses isolate global configuration."""
import os
import subprocess
import sys

env = dict(os.environ, PYTHONIOENCODING="utf-8")
env.pop("BOOTSTRAP_ADMIN_PASSWORD", None)
commands = [
    ["-m", "unittest", "test_config", "test_security", "test_storage", "test_parser", "test_monitoring", "test_watchlists", "test_feedback", "test_exporter", "test_llm", "test_observability", "test_bot_security", "test_dependencies", "test_export_jobs", "test_release", "-v"],
    ["test_suite.py"], ["test_panel_regressions.py"], ["test_ai_scoring_engine.py"],
]
if __name__ == "__main__":
    for command in commands:
        subprocess.run([sys.executable, *command], env=env, check=True)
