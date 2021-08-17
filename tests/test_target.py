"""
Unit-tests for target generation
"""

import os
import click
import pytest

from pathlib import Path as P
from textwrap import dedent

from commodore import cluster
from commodore.inventory import Inventory
from commodore.config import Config


@pytest.fixture
def data():
    """
    Setup test data
    """

    tenant = {
        "id": "mytenant",
        "displayName": "My Test Tenant",
    }
    cluster = {
        "id": "mycluster",
        "displayName": "My Test Cluster",
        "tenant": tenant["id"],
        "facts": {
            "distribution": "rancher",
            "cloud": "cloudscale",
        },
        "dynamicFacts": {
            "kubernetes_version": {
                "major": "1",
                "minor": "21",
                "gitVersion": "v1.21.3",
            }
        },
        "gitRepo": {
            "url": "ssh://git@git.example.com/cluster-catalogs/mycluster",
        },
    }
    return {
        "cluster": cluster,
        "tenant": tenant,
    }


def cluster_from_data(data) -> cluster.Cluster:
    return cluster.Cluster(data["cluster"], data["tenant"])


def _setup_working_dir(inv: Inventory, components):
    for cls in components:
        defaults = inv.defaults_file(cls)
        os.makedirs(defaults.parent, exist_ok=True)
        defaults.touch()
        component = inv.component_file(cls)
        os.makedirs(component.parent, exist_ok=True)
        component.touch()


def test_render_bootstrap_target(tmp_path: P):
    components = ["foo", "bar"]
    inv = Inventory(work_dir=tmp_path)
    _setup_working_dir(inv, components)

    target = cluster.render_target(inv, "cluster", ["foo", "bar", "baz"])

    classes = [
        "params.cluster",
        "defaults.foo",
        "defaults.bar",
        "global.commodore",
    ]
    assert target != ""
    print(target)
    assert len(target["classes"]) == len(
        classes
    ), "rendered target includes different amount of classes"
    for i in range(len(classes)):
        assert target["classes"][i] == classes[i]
    assert target["parameters"]["_instance"] == "cluster"


def test_render_target(tmp_path: P):
    components = ["foo", "bar"]
    inv = Inventory(work_dir=tmp_path)
    _setup_working_dir(inv, components)

    target = cluster.render_target(inv, "foo", ["foo", "bar", "baz"])

    classes = [
        "params.cluster",
        "defaults.foo",
        "defaults.bar",
        "global.commodore",
        "components.foo",
    ]
    assert target != ""
    print(target)
    assert len(target["classes"]) == len(
        classes
    ), "rendered target includes different amount of classes"
    for i in range(len(classes)):
        assert target["classes"][i] == classes[i]
    assert target["parameters"]["kapitan"]["vars"]["target"] == "foo"
    assert target["parameters"]["_instance"] == "foo"


def test_render_aliased_target(tmp_path: P):
    components = ["foo", "bar"]
    inv = Inventory(work_dir=tmp_path)
    _setup_working_dir(inv, components)

    target = cluster.render_target(inv, "fooer", ["foo", "bar", "baz"], component="foo")

    classes = [
        "params.cluster",
        "defaults.foo",
        "defaults.bar",
        "global.commodore",
        "components.foo",
    ]
    assert target != ""
    print(target)
    assert len(target["classes"]) == len(
        classes
    ), "rendered target includes different amount of classes"
    for i in range(len(classes)):
        assert target["classes"][i] == classes[i]
    assert target["parameters"]["kapitan"]["vars"]["target"] == "fooer"
    assert target["parameters"]["foo"] == "${fooer}"
    assert target["parameters"]["_instance"] == "fooer"


def test_render_aliased_target_with_dash(tmp_path: P):
    components = ["foo-comp", "bar"]
    inv = Inventory(work_dir=tmp_path)
    _setup_working_dir(inv, components)

    target = cluster.render_target(
        inv, "foo-1", ["foo-comp", "bar", "baz"], component="foo-comp"
    )

    classes = [
        "params.cluster",
        "defaults.foo-comp",
        "defaults.bar",
        "global.commodore",
        "components.foo-comp",
    ]
    assert target != ""
    print(target)
    assert len(target["classes"]) == len(
        classes
    ), "rendered target includes different amount of classes"
    for i in range(len(classes)):
        assert target["classes"][i] == classes[i]
    assert target["parameters"]["kapitan"]["vars"]["target"] == "foo-1"
    assert target["parameters"]["foo_comp"] == "${foo_1}"
    assert target["parameters"]["_instance"] == "foo-1"


def test_render_params(data, tmp_path: P):
    cfg = Config(work_dir=tmp_path)
    target = cfg.inventory.bootstrap_target
    params = cluster.render_params(cfg.inventory, cluster_from_data(data))

    assert "parameters" in params

    params = params["parameters"]
    assert "cluster" in params

    assert "name" in params["cluster"]
    assert params["cluster"]["name"] == "mycluster"

    assert target in params
    target_params = params[target]

    assert "name" in target_params
    assert target_params["name"] == "mycluster"
    assert "display_name" in target_params
    assert target_params["display_name"] == "My Test Cluster"
    assert "catalog_url" in target_params
    assert (
        target_params["catalog_url"]
        == "ssh://git@git.example.com/cluster-catalogs/mycluster"
    )
    assert "tenant" in target_params
    assert target_params["tenant"] == "mytenant"
    assert "tenant_display_name" in target_params
    assert target_params["tenant_display_name"] == "My Test Tenant"
    assert "dist" in target_params
    assert target_params["dist"] == "rancher"

    assert "facts" in params
    assert params["facts"] == data["cluster"]["facts"]

    assert "dynamic_facts" in params
    dyn_facts = params["dynamic_facts"]
    assert "kubernetes_version" in dyn_facts
    k8s_ver = dyn_facts["kubernetes_version"]
    assert "major" in k8s_ver
    assert "minor" in k8s_ver
    assert "gitVersion" in k8s_ver
    assert "1" == k8s_ver["major"]
    assert "21" == k8s_ver["minor"]
    assert "v1.21.3" == k8s_ver["gitVersion"]

    assert "cloud" in params
    assert "provider" in params["cloud"]
    assert params["cloud"]["provider"] == "cloudscale"

    assert "customer" in params
    assert "name" in params["customer"]
    assert params["customer"]["name"] == "mytenant"


def test_missing_facts(data, tmp_path: P):
    data["cluster"]["facts"].pop("cloud")
    cfg = Config(work_dir=tmp_path)
    with pytest.raises(click.ClickException):
        cluster.render_params(cfg.inventory, cluster_from_data(data))


def test_empty_facts(data, tmp_path: P):
    data["cluster"]["facts"]["cloud"] = ""
    cfg = Config(work_dir=tmp_path)
    with pytest.raises(click.ClickException):
        cluster.render_params(cfg.inventory, cluster_from_data(data))


def test_read_cluster_and_tenant(tmp_path):
    cfg = Config(work_dir=tmp_path)
    file = cfg.inventory.params_file
    os.makedirs(file.parent, exist_ok=True)
    with open(file, "w") as f:
        f.write(
            dedent(
                """
            parameters:
              cluster:
                name: c-twilight-water-9032
                tenant: t-delicate-pine-3938"""
            )
        )

    cluster_id, tenant_id = cluster.read_cluster_and_tenant(cfg.inventory)
    assert cluster_id == "c-twilight-water-9032"
    assert tenant_id == "t-delicate-pine-3938"


def test_read_cluster_and_tenant_missing_fact(tmp_path):
    inv = Inventory(work_dir=tmp_path)
    file = inv.params_file
    os.makedirs(file.parent, exist_ok=True)
    with open(file, "w") as f:
        f.write(
            dedent(
                """
            classes: []
            parameters: {}"""
            )
        )

    with pytest.raises(KeyError):
        cluster.read_cluster_and_tenant(inv)
