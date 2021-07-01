import copy
import os
import pytest
import re
import yaml

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from typing import Iterable

import git

from commodore.cluster import Cluster
from commodore.config import Config

import commodore.compile as commodore_compile


@pytest.fixture
def config(tmp_path: Path):
    """
    Setup config object for tests
    """

    return Config(
        tmp_path,
        api_url="https://syn.example.com",
        api_token="token",
    )


cluster_resp = {
    "id": "c-test",
    "tenant": "t-test",
    "displayName": "test-cluster",
    "facts": {
        "cloud": "local",
        "distribution": "k3s",
    },
    "gitRepo": {
        "url": "ssh://git@github.com/projectsyn/test-cluster-catalog.git",
    },
}

tenant_resp = {
    "id": "t-test",
    "displayName": "Test tenant",
    "gitRepo": {
        "url": "https://github.com/projectsyn/test-tenant.git",
    },
    "globalGitRepoURL": "https://github.com/projectsyn/commodore-defaults.git",
}


def _mock_load_cluster_from_api(cfg: Config, cluster_id: str):
    assert cluster_id == "c-test"
    return Cluster(cluster_resp, tenant_resp)


def _verify_target(
    target_dir: Path, expected_classes: Iterable[str], tname: str, bootstrap=False
):
    tpath = target_dir / f"{tname}.yml"
    assert tpath.is_file()
    classes = copy.copy(expected_classes)
    if not bootstrap:
        classes.append(f"components.{tname}")
    with open(tpath) as t:
        tcontents = yaml.safe_load(t)
        assert all(k in tcontents for k in ["classes", "parameters"])
        assert tcontents["classes"] == classes
        tparams = tcontents["parameters"]
        assert "_instance" in tparams
        assert tparams["_instance"] == tname
        assert bootstrap or (
            "kapitan" in tparams
            and "vars" in tparams["kapitan"]
            and "target" in tparams["kapitan"]["vars"]
            and tparams["kapitan"]["vars"]["target"] == tname
        )


def _verify_commit_message(
    tmp_path: Path,
    config: Config,
    commit_msg: str,
    short_sha_len: int,
    catalog_repo: git.Repo,
):
    """
    Parse and check catalog commit message
    """

    rev_re_fragment = fr"(?P<commit_sha>[0-9a-f]{{{short_sha_len}}})"

    component_commit_re = re.compile(
        r"^ \* (?P<component_name>[a-z-]+): "
        + r"(?P<component_version>(None|v[0-9]+.[0-9]+.[0-9]+|[a-z0-9]{40})) "
        + fr"\({rev_re_fragment}\)$"
    )
    global_commit_re = re.compile(fr"^ \* global: {rev_re_fragment}$")
    tenant_commit_re = re.compile(fr"^ \* customer: {rev_re_fragment}$")
    compile_ts_re = re.compile(r"^Compilation timestamp: (?P<ts>[0-9T.:-]+)$")

    global_rev = git.Repo(tmp_path / "inventory/classes/global").head.commit.hexsha[
        :short_sha_len
    ]
    tenant_rev = git.Repo(tmp_path / "inventory/classes/t-test").head.commit.hexsha[
        :short_sha_len
    ]
    catalog_commit_ts = catalog_repo.head.commit.committed_datetime

    assert commit_msg.startswith(
        "Automated catalog update from Commodore\n\nComponent commits:\n"
    )
    commit_msg_lines = commit_msg.split("\n")[3:]

    components = config.get_components()
    component_count = len(components.keys())

    component_lines = commit_msg_lines[:component_count]
    for line in component_lines:
        m = component_commit_re.match(line)
        assert m, f"Unable to parse component commit line {line}"
        cname = m.group("component_name")
        assert cname in components
        c = components[cname]
        assert str(c.version) == m.group("component_version")
        assert c.repo.head.commit.hexsha[:short_sha_len] == m.group("commit_sha")

    # Remaining lines should be config commit shas and compilation timestamp
    rem_lines = commit_msg_lines[component_count:]
    assert len(rem_lines) == 7

    # empty line before configuration commits
    assert rem_lines[0] == ""

    assert rem_lines[1] == "Configuration commits:"
    global_match = global_commit_re.match(rem_lines[2])
    assert global_match, "Could not parse global repo commit"
    assert global_rev == global_match.group("commit_sha")
    tenant_match = tenant_commit_re.match(rem_lines[3])
    assert tenant_match, "Could not parse tenant repo commit"
    assert tenant_rev == tenant_match.group("commit_sha")

    # empty line after config commits
    assert rem_lines[4] == ""

    compile_ts_match = compile_ts_re.match(rem_lines[5])
    assert compile_ts_match
    compile_ts_str = compile_ts_match.group("ts")
    compile_ts = datetime.fromisoformat(compile_ts_str)
    # if compile timestamp doesn't have tzinfo, set same tzinfo as committed ts
    if compile_ts.tzinfo is None:
        compile_ts = compile_ts.replace(tzinfo=catalog_commit_ts.tzinfo)
    print(abs(compile_ts - catalog_commit_ts))
    # Commit message timestamp and commit timestamp should be within 1 second of each other
    assert abs(compile_ts - catalog_commit_ts) < timedelta(seconds=1)

    # last line empty due to trailing \n in commit message
    assert rem_lines[6] == ""


@pytest.mark.integration
@patch.object(
    commodore_compile,
    "load_cluster_from_api",
    side_effect=_mock_load_cluster_from_api,
)
def test_catalog_compile(load_cluster, config: Config, tmp_path: Path, capsys):
    os.chdir(tmp_path)
    cluster_id = "c-test"
    expected_components = ["argocd", "metrics-server"]
    expected_dirs = [
        tmp_path / "catalog",
        tmp_path / "catalog/manifests",
        tmp_path / "catalog/refs",
        tmp_path / "compiled",
        tmp_path / "dependencies",
        tmp_path / "dependencies/lib",
        tmp_path / "dependencies/libs",
        tmp_path / "inventory",
        tmp_path / "inventory/classes/components",
        tmp_path / "inventory/classes/defaults",
        tmp_path / "inventory/classes/global",
        tmp_path / "inventory/classes/t-test",
        tmp_path / "inventory/classes/params",
        tmp_path / "inventory/targets",
        tmp_path / "vendor",
        tmp_path / "vendor/lib",
    ]
    expected_classes = ["params.cluster"]
    for c in expected_components:
        expected_dirs.extend(
            [
                tmp_path / "dependencies" / c,
                tmp_path / "vendor" / c,
            ]
        )
        expected_classes.append(f"defaults.{c}")
    expected_classes.append("global.commodore")

    config.push = True
    commodore_compile.compile(config, cluster_id)

    # Verify our mocked load cluster was called
    assert load_cluster.called

    # Stdout success msg
    captured = capsys.readouterr()
    assert "Catalog compiled!" in captured.out

    # Check config for expected components
    assert sorted(config.get_components().keys()) == sorted(expected_components)

    # Output dirs
    for output_dir in expected_dirs:
        assert output_dir.is_dir()

    # Verify params.cluster
    with open(tmp_path / "inventory/classes/params/cluster.yml") as f:
        fcontents = yaml.safe_load(f)
        assert "parameters" in fcontents
        params = fcontents["parameters"]
        assert all(k in params for k in ["cloud", "cluster", "customer", "facts"])
        assert "provider" in params["cloud"]
        assert params["cloud"]["provider"] == cluster_resp["facts"]["cloud"]
        assert all(
            k in params["cluster"] for k in ["catalog_url", "dist", "name", "tenant"]
        )
        assert params["cluster"]["catalog_url"] == cluster_resp["gitRepo"]["url"]
        assert params["cluster"]["dist"] == cluster_resp["facts"]["distribution"]
        assert params["cluster"]["name"] == cluster_resp["id"]
        assert params["cluster"]["tenant"] == cluster_resp["tenant"]
        assert "name" in params["customer"]
        assert params["customer"]["name"] == cluster_resp["tenant"]
        for k, v in params["facts"].items():
            assert v == cluster_resp["facts"][k]

    # TODO: Targets
    target_dir = tmp_path / "inventory/targets"

    _verify_target(target_dir, expected_classes, "cluster", bootstrap=True)
    for cn in expected_components:
        _verify_target(target_dir, expected_classes, cn)

    # Catalog checks
    catalog_manifests = tmp_path / "catalog/manifests"
    found_components = {cn: False for cn in expected_components}
    for f in (catalog_manifests / "apps").iterdir():
        for c in expected_components:
            if c in f.name:
                found_components[c] = True
    assert all(found_components)

    short_sha_len = 6

    catalog_repo = git.Repo(tmp_path / "catalog")
    commit_msg = catalog_repo.head.commit.message

    _verify_commit_message(tmp_path, config, commit_msg, short_sha_len, catalog_repo)

    assert not catalog_repo.is_dirty()
    assert not catalog_repo.untracked_files
