import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_import_package_without_optional_framework_side_effects() -> None:
    # Given: a fresh Python interpreter importing only the core package.
    import_check = (
        "import sys\n"
        "import my_usermanager\n"
        "assert my_usermanager.__version__ == '0.6.7'\n"
        "assert 'my_auth' not in sys.modules\n"
        "assert 'fastapi' not in sys.modules\n"
        "assert 'pydantic' not in sys.modules\n"
        "assert 'joserfc' not in sys.modules\n"
        "assert 'authlib' not in sys.modules\n"
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


def test_release_metadata_absorbs_unreleased_work() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    changelog = (REPO_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    extras = project["project"]["optional-dependencies"]
    version = project["project"]["version"]

    assert version == "0.6.7"
    unreleased = changelog.split("## Unreleased", 1)[1].split("## ", 1)[0]
    released = changelog.split("## 0.6.7", 1)[1].split("## ", 1)[0]
    assert "install_local_identity" not in unreleased
    assert "#152" not in unreleased
    assert "#129" not in unreleased
    assert "install_local_identity" in released
    assert "#152" in released
    assert "#129" in released
    assert "v0.7.2" in released
    assert "## 0.6.6" in changelog
    assert "/oidc/login" in changelog.split("## 0.6.6", 1)[1].split("## ", 1)[0]
    assert "dev" not in extras
    assert "oidc" in extras
    assert (REPO_ROOT / "src" / "my_usermanager" / "py.typed").is_file()
    assert "Typing :: Typed" in project["project"]["classifiers"]
    assert project["tool"]["uv"]["sources"]["my-auth"]["tag"] == "v0.5.6"
    assert project["tool"]["uv"]["sources"]["app-factory"]["tag"] == "v0.7.2"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "my-auth v0.4 enrollment" not in readme
    assert "`my-auth` 0.5 enrollment" in readme
    agents = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "v0.7.2" in agents
    assert "v0.5.6" in agents
    assert "v0.6.7" in agents.split("## Preferred BOM", 1)[1]
    assert "install_local_identity" in agents
    assert "v0.6.22" not in agents.split("## Preferred BOM", 1)[1]
