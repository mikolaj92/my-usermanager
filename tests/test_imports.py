import subprocess
import sys


def test_import_package_without_optional_framework_side_effects() -> None:
    # Given: a fresh Python interpreter importing only the core package.
    import_check = (
        "import sys\n"
        "import my_usermanager\n"
        "assert my_usermanager.__version__ == '0.1.0'\n"
        "assert 'my_auth' not in sys.modules\n"
        "assert 'fastapi' not in sys.modules\n"
        "assert 'pydantic' not in sys.modules\n"
    )

    # When: the import check is executed in that interpreter.
    completed = subprocess.run(
        [sys.executable, "-c", import_check],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )

    # Then: the assertions pass without noisy output.
    assert completed.stdout == ""
    assert completed.stderr == ""
