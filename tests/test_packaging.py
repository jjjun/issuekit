from importlib import resources
from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def load_pyproject() -> dict[str, object]:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def test_mcp_extra_installs_mcp_dependency() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]

    optional_dependencies = project["optional-dependencies"]
    mcp_extra = optional_dependencies["mcp"]

    assert any(requirement.startswith("mcp>=") for requirement in mcp_extra)


def test_core_project_dependencies_are_intentional() -> None:
    pyproject = load_pyproject()
    project = pyproject["project"]

    assert project["dependencies"] == ["httpx>=0.27,<1"]


def test_cli_import_does_not_require_mcp_extra() -> None:
    from issuekit import cli

    assert callable(cli.main)


def test_issuekit_mcp_script_is_declared() -> None:
    pyproject = load_pyproject()
    scripts = pyproject["project"]["scripts"]

    assert scripts["issuekit-mcp"] == "issuekit.mcp.server:main"


def test_prompt_templates_load_as_package_resources() -> None:
    from issuekit.prompts import TEMPLATE_NAMES

    template_dir = resources.files("issuekit.prompts").joinpath("templates")
    for template_name in TEMPLATE_NAMES:
        assert template_dir.joinpath(template_name).read_text(encoding="utf-8").strip()
