"""
Tests for component new command
"""
import os
import pytest
import yaml
from pathlib import Path as P
from subprocess import call
from git import Repo

from test_component import setup_directory


def test_run_component_new_command(tmp_path: P):
    """
    Run the component new command
    """

    setup_directory(tmp_path)

    component_name = "test-component"
    exit_status = call(
        f"commodore -d '{tmp_path}' -vvv component new {component_name} --lib --pp",
        shell=True,
    )
    assert exit_status == 0
    for file in [
        P("README.md"),
        P("renovate.json"),
        P("class", f"{component_name}.yml"),
        P("component", "main.jsonnet"),
        P("component", "app.jsonnet"),
        P("lib", f"{component_name}.libsonnet"),
        P("docs", "modules", "ROOT", "pages", "references", "parameters.adoc"),
        P("docs", "modules", "ROOT", "pages", "index.adoc"),
        P(".github", "changelog-configuration.json"),
        P(".github", "PULL_REQUEST_TEMPLATE.md"),
        P(".github", "workflows", "release.yaml"),
        P(".github", "workflows", "test.yaml"),
        P(".github", "ISSUE_TEMPLATE", "01_bug_report.md"),
        P(".github", "ISSUE_TEMPLATE", "02_feature_request.md"),
        P(".github", "ISSUE_TEMPLATE", "config.yml"),
    ]:
        assert (tmp_path / "dependencies" / component_name / file).exists()
    # Check that there are no uncommited files in the component repo
    repo = Repo(tmp_path / "dependencies" / component_name)
    assert not repo.is_dirty()
    assert not repo.untracked_files
    # Verify component class
    with open(
        tmp_path / "dependencies" / component_name / "class" / f"{component_name}.yml"
    ) as cclass:
        class_contents = yaml.safe_load(cclass)
        assert "parameters" in class_contents
        params = class_contents["parameters"]
        assert "kapitan" in params
        assert "commodore" in params
        assert "postprocess" in params["commodore"]
        assert "filters" in params["commodore"]["postprocess"]
        assert isinstance(params["commodore"]["postprocess"]["filters"], list)


def test_run_component_new_command_with_name(tmp_path: P):
    """
    Run the component new command with the slug option set
    """

    setup_directory(tmp_path)

    component_name = "Component with custom name"
    component_slug = "named-component"
    readme_path = tmp_path / "dependencies" / component_slug / "README.md"

    exit_status = call(
        f"commodore -d {tmp_path} -vvv component new --name '{component_name}' {component_slug}",
        shell=True,
    )

    assert exit_status == 0
    assert os.path.exists(readme_path)

    with open(readme_path, "r") as file:
        data = file.read()
        assert component_name in data
        assert component_slug not in data


@pytest.mark.parametrize(
    "test_input",
    [
        "component-test-illegal",
        "test-illegal-",
        "-test-illegal",
        "00-test-illegal",
        "TestIllegal",
        "test_illegal",
    ],
)
def test_run_component_new_command_with_illegal_slug(tmp_path: P, test_input):
    """
    Run the component new command with an illegal slug
    """
    setup_directory(tmp_path)
    exit_status = call(
        f"commodore -d {tmp_path} -vvv component new {test_input}", shell=True
    )
    assert exit_status != 0


def test_run_component_new_then_delete(tmp_path: P):
    """
    Create a new component, then immediately delete it.
    """
    setup_directory(tmp_path)

    component_name = "test-component"
    exit_status = call(
        f"commodore -d {tmp_path} -vvv component new {component_name} --lib --pp",
        shell=True,
    )
    assert exit_status == 0

    exit_status = call(
        f"commodore -d {tmp_path} -vvv component delete --force {component_name}",
        shell=True,
    )
    assert exit_status == 0

    # Ensure the dependencies folder is gone.
    assert not (tmp_path / "dependencies" / component_name).exists()

    # Links in the inventory should be gone too.
    for f in [
        tmp_path / "inventory" / "classes" / "components" / f"{component_name}.yml",
        tmp_path / "inventory" / "classes" / "defaults" / f"{component_name}.yml",
        tmp_path / "dependencies" / "lib" / f"{component_name}.libsonnet",
        tmp_path / "vendor" / component_name,
    ]:
        assert not f.exists()

    assert not (tmp_path / "inventory" / "targets" / f"{component_name}.yml").exists()


def test_deleting_inexistant_component(tmp_path: P):
    """
    Trying to delete a component that does not exist results in a non-0 exit
    code.
    """
    setup_directory(tmp_path)
    component_name = "i-dont-exist"

    exit_status = call(
        f"commodore -d {tmp_path} -vvv component delete --force {component_name}",
        shell=True,
    )
    assert exit_status == 2


@pytest.mark.parametrize(
    "extra_args",
    [
        "",
        "--lib",
        "--pp",
        "--lib --pp",
    ],
)
def test_check_component_template(tmp_path: P, extra_args: str):
    """
    Run integrated lints in freshly created component
    """

    setup_directory(tmp_path)

    component_name = "test-component"
    exit_status = call(
        f"commodore -d {tmp_path} -vvv component new {component_name} {extra_args}",
        shell=True,
    )
    assert exit_status == 0

    # Call `make lint` in component directory
    exit_status = call(
        "make lint",
        shell=True,
        cwd=tmp_path / "dependencies" / component_name,
    )
    assert exit_status == 0
