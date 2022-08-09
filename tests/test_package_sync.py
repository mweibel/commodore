from __future__ import annotations

import json

from pathlib import Path
from unittest.mock import patch

import click
import git
import github
import pytest
import responses
import yaml

from conftest import RunnerFunc
from test_package import _setup_package_remote
from test_package_template import call_package_new

from commodore.config import Config
from commodore.gitrepo import GitRepo
from commodore.package import Package
from commodore.package import sync
from commodore.package.template import PackageTemplater

DATA_DIR = Path(__file__).parent.absolute() / "testdata" / "github"


def create_pkg_list(tmp_path: Path) -> Path:
    pkg_list = tmp_path / "pkgs.yaml"
    with open(pkg_list, "w", encoding="utf-8") as f:
        yaml.safe_dump(["projectsyn/package-foo"], f)

    return pkg_list


@pytest.mark.parametrize("sync_branch", ["none", "local", "remote"])
def test_ensure_branch(tmp_path: Path, config: Config, sync_branch: str):
    _setup_package_remote("foo", tmp_path / "foo.git")
    if sync_branch == "remote":
        r = git.Repo(tmp_path / "foo.git")
        r.create_head("template-sync")
    p = Package.clone(config, f"file://{tmp_path}/foo.git", "foo")
    if sync_branch == "local":
        orig_head = p.repo.repo.head
        p.repo.repo.create_head("template-sync")

        p.checkout()
        assert p.repo.repo.head == orig_head

    with open(p.target_dir / "test.txt", "w", encoding="utf-8") as f:
        f.write("Hello, world\n")
    p.repo.commit("Add test.txt")

    r = p.repo.repo

    assert any(h.name == "template-sync" for h in r.heads) == (sync_branch == "local")

    sync.ensure_branch(p)

    hs = [h for h in r.heads if h.name == "template-sync"]
    assert len(hs) == 1

    h = hs[0]
    assert h.commit.message == "Add test.txt"


API_TOKEN_MATCHER = responses.matchers.header_matcher(
    {"Authorization": "token ghp_fake-token"}
)


def _setup_gh_get_responses(has_open_pr: bool, clone_url: str = ""):

    with open(DATA_DIR / "projectsyn-package-foo-response.json", encoding="utf-8") as f:
        resp = json.load(f)
        if clone_url:
            resp["clone_url"] = clone_url
        responses.add(
            responses.GET,
            "https://api.github.com:443/repos/projectsyn/package-foo",
            status=200,
            json=resp,
            match=[API_TOKEN_MATCHER],
        )

    if has_open_pr:
        with open(
            DATA_DIR / "projectsyn-package-foo-response-pulls.json", encoding="utf-8"
        ) as f:
            pulls = json.load(f)
    else:
        pulls = []
    responses.add(
        responses.GET,
        "https://api.github.com:443/repos/projectsyn/package-foo/pulls",
        json=pulls,
        status=200,
        match=[API_TOKEN_MATCHER],
    )


def labels_post_body_match(request) -> tuple[bool, str]:
    """Custom matcher for the labels API POST request body.

    `responses.matchers.json_params_matcher()` doesn't support top-level JSON
    list, but PyGitHub just posts a top-level list when updating labels, so we
    implement our own matcher function."""
    reason = ""
    request_body = request.body
    try:
        if isinstance(request_body, bytes):
            request_body = request_body.decode("utf-8")
        json_body = json.loads(request_body) if request_body else []

        valid = json_body == ["template-sync"]

        if not valid:
            reason = "request.body doesn't match: {} doesn't match {}".format(
                json_body, ["template-sync"]
            )

    except json.JSONDecodeError:
        valid = False
        reason = (
            "request.body doesn't match: JSONDecodeError: Cannot parse request.body"
        )

    return valid, reason


def _setup_gh_pr_response(method, pr_body=""):
    with open(
        DATA_DIR / "projectsyn-package-foo-response-pr.json", encoding="utf-8"
    ) as f:
        resp = json.load(f)
        suffix = ""
        body_matcher = responses.matchers.json_params_matcher(
            {
                "title": "Update from package template",
                "body": pr_body,
                "draft": False,
                "base": "master",
                "head": "template-sync",
            }
        )
        if method == responses.PATCH:
            suffix = "/1"
            body_matcher = responses.matchers.json_params_matcher({"body": ""})
        responses.add(
            method,
            f"https://api.github.com:443/repos/projectsyn/package-foo/pulls{suffix}",
            json=resp,
            status=200,
            match=[API_TOKEN_MATCHER, body_matcher],
        )

    if method == responses.POST:
        label_resp = [
            {
                "id": 4405096203,
                "node_id": "LA_kwDOHyQSds8AAAABBpBvCw",
                "url": "https://api.github.com/repos/projectsyn/package-foo/labels/template-sync",
                "name": "template-sync",
                "color": "ededed",
                "default": False,
                "description": None,
            }
        ]

        responses.add(
            responses.POST,
            "https://api.github.com:443/repos/projectsyn/package-foo/issues/1/labels",
            json=label_resp,
            status=200,
            match=[API_TOKEN_MATCHER, labels_post_body_match],
        )


@responses.activate
@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("pr_exists", [True, False])
def test_ensure_pr(
    capsys, tmp_path: Path, config: Config, dry_run: bool, pr_exists: bool
):
    _setup_gh_get_responses(pr_exists)
    if not dry_run:
        _setup_gh_pr_response(responses.PATCH if pr_exists else responses.POST)
    _setup_package_remote("foo", tmp_path / "foo.git")
    config.github_token = "ghp_fake-token"
    p = Package.clone(config, f"file://{tmp_path}/foo.git", "foo")
    pname = "projectsyn/package-foo"
    sync.ensure_branch(p)

    gh = github.Github(config.github_token)
    gr = gh.get_repo(pname)

    sync.ensure_pr(p, pname, gr, dry_run)

    if dry_run:
        captured = capsys.readouterr()
        cu = "update" if pr_exists else "create"
        assert f"Would {cu} PR for {pname}" in captured.out
        assert len(responses.calls) == 2
    else:
        assert len(responses.calls) == 3 + (1 if not pr_exists else 0)


@pytest.mark.parametrize(
    "ghtoken,package_list_contents",
    [
        (None, ""),
        ("ghp_token", '"foo"'),
        ("ghp_token", 'foo: "bar"'),
        ("ghp_token", "foo: bar:"),
        ("ghp_token", "fff: bar\n- foo"),
    ],
)
def test_sync_packages_package_list_parsing(
    tmp_path: Path, config: Config, ghtoken, package_list_contents
):
    config.github_token = ghtoken
    pkg_list = tmp_path / "pkgs.yaml"
    with open(pkg_list, "w", encoding="utf-8") as f:
        f.write(package_list_contents)

    with pytest.raises(click.ClickException) as exc:
        sync.sync_packages(config, pkg_list, False)

    if ghtoken is None:
        assert str(exc.value) == "Can't continue, missing GitHub API token."
    elif package_list_contents.endswith(":") or package_list_contents.startswith("fff"):
        # parse error
        assert str(exc.value) == f"Failed to parse YAML in '{pkg_list}'"
    else:
        # type error
        typ = "<class 'dict'>" if ":" in package_list_contents else "<class 'str'>"
        assert (
            str(exc.value)
            == f"Expected a list in '{pkg_list}', but got unexpected type: {typ}"
        )


def make_mock_package_templater(remote_url: str):
    """Create a Mock package templater class which overrides property `repo_url` with
    the provided remote_url string.

    Use as follows:

        with patch(
            "commodore.package.template.PackageTemplater",
            new_callable=lambda: make_mock_package_templater("file://path/to/remote.git",
        ):
            function_under_test()
    """

    class MockPkgTemplater(PackageTemplater):
        fake_url = remote_url

        @property
        def repo_url(self) -> str:
            return self.fake_url

    return MockPkgTemplater


@pytest.mark.parametrize("dry_run", [False, True])
@responses.activate
def test_sync_packages(
    tmp_path: Path, cli_runner: RunnerFunc, config: Config, dry_run: bool
):
    config.github_token = "ghp_fake-token"
    responses.add_passthru("https://github.com")
    remote_path = tmp_path / "remote.git"
    remote_url = f"file://{remote_path}"
    rem = git.Repo.init(remote_path, bare=True)
    _setup_gh_get_responses(False, clone_url=remote_url)

    # Get template latest commit sha
    tpl = git.Repo.clone_from(
        "https://github.com/projectsyn/commodore-config-package-template.git",
        tmp_path / "template.git",
    )
    tpl_head_name = tpl.head.reference.name
    tpl_head_short = tpl.head.commit.hexsha[:7]

    pr_body = f"Template version: {tpl_head_name} ({tpl_head_short})"
    if not dry_run:
        _setup_gh_pr_response(responses.POST, pr_body=pr_body)

    # Create package with old version
    call_package_new(
        tmp_path, cli_runner, "foo", template_version="--template-version=main^"
    )
    pkg_path = tmp_path / "dependencies" / "pkg.foo"
    with open(pkg_path / ".cruft.json", "r", encoding="utf-8") as f:
        cruft_json = json.load(f)

    # Adjust template version, so sync has something to update
    cruft_json["checkout"] = "main"
    # Write back adjusted .cruft.json and amend initial commit
    with open(pkg_path / ".cruft.json", "w", encoding="utf-8") as f:
        json.dump(cruft_json, f, indent=2)
    r = GitRepo(None, pkg_path)
    r.stage_files([".cruft.json"])
    r.commit("Initial commit", amend=True)

    # Set fake remote for the test package
    r.repo.remote().set_url(remote_url)
    r.push()
    assert rem.head.commit == r.repo.head.commit

    # Setup package list
    pkg_list = create_pkg_list(tmp_path)

    with patch(
        "commodore.package.template.PackageTemplater",
        new_callable=lambda: make_mock_package_templater(remote_url),
    ):
        sync.sync_packages(config, pkg_list, dry_run)

    assert len(responses.calls) == 2 + (2 if not dry_run else 0)
    assert r.repo.head.commit.message == f"Update from template\n\n{pr_body}"


@responses.activate
def test_sync_packages_skip(tmp_path: Path, config: Config, capsys):
    config.github_token = "ghp_fake-token"

    pkg_dir = tmp_path / "package-foo"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    _setup_gh_get_responses(False, clone_url=f"file://{pkg_dir}")

    # setup non-package repo
    with open(pkg_dir / "test.txt", "w", encoding="utf-8") as f:
        f.write("Hello, world!\n")
    r = git.Repo.init(pkg_dir)
    r.index.add("test.txt")
    r.index.commit("Initial commit")

    pkg_list = create_pkg_list(tmp_path)

    sync.sync_packages(config, pkg_list, True)

    captured = capsys.readouterr()
    assert (
        " > Skipping repo projectsyn/package-foo which doesn't have `.cruft.json`"
        in captured.out
    )


@responses.activate
def test_sync_packages_skip_missing(capsys, tmp_path: Path, config: Config):
    config.github_token = "ghp_fake-token"
    pkg_list = create_pkg_list(tmp_path)

    responses.add(
        responses.GET,
        "https://api.github.com:443/repos/projectsyn/package-foo",
        json={
            "message": "Not Found",
            "documentation_url": "https://docs.github.com/rest/reference/repos#get-a-repository",
        },
        status=404,
    )

    sync.sync_packages(config, pkg_list, True)

    captured = capsys.readouterr()

    assert (
        " > Repository projectsyn/package-foo doesn't exist, skipping..."
        in captured.out
    )
