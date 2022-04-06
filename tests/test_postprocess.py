"""
Tests for postprocessing
"""
import os

import click
import pytest
import yaml
from textwrap import dedent

from commodore.config import Config
from commodore.component import Component
from commodore.postprocess import postprocess_components, builtin_filters
from test_component_template import call_component_new


def _make_builtin_filter(ns, enabled=None, create_namespace="false"):
    f = {
        "filters": [
            {
                "path": "test",
                "type": "builtin",
                "filter": "helm_namespace",
                "filterargs": {
                    "namespace": ns,
                    "create_namespace": create_namespace,
                },
            }
        ]
    }
    if enabled is not None:
        f["filters"][0]["enabled"] = enabled
    return f


def _make_jsonnet_filter(tmp_path, ns, enabled=None):
    filter_file = (
        tmp_path / "dependencies" / "test-component" / "postprocess" / "filter.jsonnet"
    )
    os.makedirs(filter_file.parent)
    with open(filter_file, "w") as ff:
        ff.write(
            dedent(
                """
                local com = import 'lib/commodore.libjsonnet';
                local file = std.extVar('output_path');
                local objs = com.yaml_load_all(file);
                local stem(elem) =
                    local elems = std.split(elem, '.');
                    std.join('.', elems[:std.length(elems) - 1]);
                local fixup(objs) = [ obj { metadata+: { namespace: 'myns' }} for obj in objs ];
                {
                    [stem(file)]: fixup(objs),
                }
                """
            )
        )

    f = {
        "filters": [
            {
                "path": "test/object.yaml",
                "type": "jsonnet",
                "filter": "postprocess/filter.jsonnet",
            }
        ]
    }

    if enabled is not None:
        f["filters"][0]["enabled"] = enabled
    return f


def _make_ns_filter(
    tmp_path, ns, enabled=None, jsonnet=False, create_namespace="false"
):
    if jsonnet:
        return _make_jsonnet_filter(tmp_path, ns, enabled=enabled)

    return _make_builtin_filter(ns, enabled=enabled, create_namespace=create_namespace)


def _setup(tmp_path, f, alias="test-component"):
    targetdir = tmp_path / "compiled" / alias / "test"
    os.makedirs(targetdir, exist_ok=True)

    libdir = tmp_path / "vendor" / "lib"
    os.makedirs(libdir, exist_ok=True)

    with open(libdir / "kube.libjsonnet", "w") as kf:
        kf.write(
            dedent(
                """
                {
                    Namespace(name): {
                        apiVersion: "v1",
                        kind: "Namespace",
                        metadata: {
                            name: name,
                        },
                    }
                }"""
            )
        )

    testf = targetdir / "object.yaml"
    with open(testf, "w") as objf:
        obj = {
            "metadata": {
                "name": "test",
                "namespace": "untouched",
            },
            "kind": "Secret",
            "apiVersion": "v1",
            "stringData": {
                "content": "verysecret",
            },
        }
        yaml.dump(obj, objf)

    # Create external pp filter file
    pp_file = (
        tmp_path / "dependencies" / "test-component" / "postprocess" / "filters.yml"
    )
    os.makedirs(pp_file.parent, exist_ok=True)

    config = Config(work_dir=tmp_path)
    component = Component(
        "test-component", work_dir=tmp_path, repo_url="https://fake.repo.url"
    )
    config.register_component(component)
    aliases = {alias: "test-component"}
    config.register_component_aliases(aliases)
    inventory = {
        alias: {
            "classes": {
                "defaults.test-component",
                "global.common",
                "components.test-component",
            },
            "parameters": {
                "test_component": {
                    "namespace": "syn-test-component",
                },
                "commodore": {
                    "postprocess": f,
                },
            },
        },
    }

    return testf, config, inventory, config.get_components()


def _expected_ns(enabled):
    if enabled is None or enabled:
        return "myns"
    else:
        return "untouched"


@pytest.mark.parametrize("enabled", [None, True, False])
@pytest.mark.parametrize("alias", ["test-component", "component-alias"])
@pytest.mark.parametrize(
    "jsonnet,create_namespace",
    [
        (False, False),
        (False, True),
        (False, "false"),
        (False, "true"),
        # create_namespace is not relevant for the custom Jsonnet filter
        (True, "false"),
    ],
)
def test_postprocess_components(
    tmp_path, capsys, enabled, jsonnet, alias, create_namespace
):
    call_component_new(tmp_path=tmp_path)

    f = _make_ns_filter(
        tmp_path,
        "myns",
        enabled=enabled,
        jsonnet=jsonnet,
        create_namespace=create_namespace,
    )

    testf, config, inventory, components = _setup(tmp_path, f, alias=alias)

    postprocess_components(config, inventory, components)

    assert testf.exists()
    expected_ns = _expected_ns(enabled)
    with open(testf) as objf:
        obj = yaml.safe_load(objf)
        assert obj["metadata"]["namespace"] == expected_ns

    create_ns = create_namespace
    if isinstance(create_ns, str):
        create_ns = create_ns == "true"

    if create_ns:
        nsf = testf.parent / "00_namespace.yaml"
        # Namespace should only be created if filter is enabled. Since filters are
        # implicitly enabled, enabled=None should cause the namespace to be created too.
        ns_created = enabled is None or enabled
        assert nsf.exists() == ns_created
        if ns_created:
            with open(nsf) as ns:
                obj = yaml.safe_load(ns)
                assert obj == {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": expected_ns},
                }

    if enabled is not None and not enabled:
        captured = capsys.readouterr()
        assert "Skipping disabled filter" in captured.out


@pytest.mark.parametrize(
    "error,expected",
    [
        (
            "enabled-not-bool",
            "Filter key 'enabled' is not a boolean",
        ),
        (
            "missing-key-type",
            "\"Filter is missing required key(s) {'type'}\"",
        ),
        (
            "missing-key-filter",
            "\"Filter is missing required key(s) {'filter'}\"",
        ),
        (
            "unknown-type",
            "Filter has unknown type jinja2",
        ),
        (
            "no-filter",
            "Jsonnet filter definition does not exist",
        ),
    ],
)
def test_postprocess_invalid_jsonnet_filter(
    capsys, tmp_path, error: str, expected: str
):
    call_component_new(tmp_path=tmp_path)

    f = _make_jsonnet_filter(tmp_path, "override", enabled=True)
    filtername = f["filters"][0]["filter"]
    if error == "enabled-not-bool":
        f["filters"][0]["enabled"] = "true"
    elif error == "missing-key-type":
        del f["filters"][0]["type"]
    elif error == "missing-key-filter":
        del f["filters"][0]["filter"]
        filtername = "<unknown>"
    elif error == "unknown-type":
        f["filters"][0]["type"] = "jinja2"
    elif error == "no-filter":
        f["filters"][0]["filter"] = "invalid.jsonnet"
        filtername = "invalid.jsonnet"
    else:
        raise NotImplementedError(f"Unknown test case {error}")

    testf, config, inventory, components = _setup(tmp_path, f)

    postprocess_components(config, inventory, components)

    assert testf.exists()
    captured = capsys.readouterr()
    msg = f"Skipping filter '{filtername}' with invalid definition {f['filters'][0]}: {expected}"
    assert msg in captured.out


@pytest.mark.parametrize(
    "filtername,error,expected",
    [
        (
            "helm_namespace",
            "no-namespace",
            "Builtin filter 'helm_namespace': filter argument 'namespace' is required",
        ),
        (
            "helm_namespace",
            "no-filterargs",
            "\"Builtin filter is missing required key 'filterargs'\"",
        ),
        (
            "helm_namespace",
            "invalid-output-path",
            "Builtin filter called on path which doesn't exist",
        ),
        (
            "foo_filter",
            "no-filter",
            "Unknown builtin filter: foo_filter",
        ),
    ],
)
def test_postprocess_invalid_builtin_filter(
    capsys, tmp_path, filtername: str, error: str, expected: str
):
    call_component_new(tmp_path=tmp_path)

    f = _make_builtin_filter("myns")
    f["filters"][0]["filter"] = filtername

    raises = True

    if error == "no-namespace":
        del f["filters"][0]["filterargs"]["namespace"]
    elif error == "no-filterargs":
        del f["filters"][0]["filterargs"]
        raises = False
    elif error == "invalid-output-path":
        f["filters"][0]["path"] = "does-not-exist"
        raises = False
    elif error == "no-filter":
        raises = False
    else:
        raise NotImplementedError(f"Unknown test case {error}")

    testf, config, inventory, components = _setup(tmp_path, f)

    if raises:
        with pytest.raises(click.ClickException) as e:
            postprocess_components(config, inventory, components)

        assert expected in str(e.value)

    else:
        postprocess_components(config, inventory, components)

        captured = capsys.readouterr()
        msg = f"Skipping filter '{filtername}' with invalid definition {f['filters'][0]}: {expected}"
        assert msg in captured.out


def test_postprocess_run_builtin_filter_raises_exception(tmp_path):
    config = Config(work_dir=tmp_path)
    with pytest.raises(builtin_filters.UnknownBuiltinFilter):
        builtin_filters.run_builtin_filter(
            config, {}, {}, "my-component", "foo_filter", tmp_path
        )
