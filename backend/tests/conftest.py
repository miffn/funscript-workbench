"""Even backend.main's module-level app must initialize in disposable test storage."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory


_test_app_data = TemporaryDirectory(prefix="workbench-test-app-")
_prior = {name: os.environ.get(name) for name in (
    "WORKBENCH_DATA_DIR", "WORKBENCH_ROOTS_JSON", "WORKBENCH_PREVIEW_OUTPUT_ROOT",
)}
os.environ["WORKBENCH_DATA_DIR"] = str(Path(_test_app_data.name) / "data")
os.environ["WORKBENCH_ROOTS_JSON"] = "[]"
os.environ["WORKBENCH_PREVIEW_OUTPUT_ROOT"] = str(Path(_test_app_data.name) / "previews")


def pytest_sessionfinish(session, exitstatus):
    for name, value in _prior.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    _test_app_data.cleanup()
