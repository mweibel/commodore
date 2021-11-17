import os
import pytest
import random
from pathlib import Path
from typing import Dict, Optional

from commodore.inventory import parameters
from commodore.helpers import yaml_dump


GLOBAL_PARAMS = {
    "components": {
        "tc1": {
            "url": "tc1",
            "version": "gp",
        },
        "tc2": {
            "url": "tc2",
            "version": "gp",
        },
        "tc3": {
            "url": "tc3",
            "version": "gp",
        },
        "tc4": {
            "url": "tc4",
            "version": "gp",
        },
        "tc5": {
            "url": "tc5",
            "version": "gp",
        },
    }
}

DIST_PARAMS = {
    "a": {
        "components": {
            "tc1": {"version": "a_version"},
        },
    },
    "b": {
        "components": {
            "tc2": {"url": "b_url"},
        },
    },
    "c": {"other_key": {}},
    "d": {"test": "testing"},
}

CLOUD_REGION_PARAMS = {
    "x": {
        "components": {
            "tc1": {"version": "x_version"},
        },
    },
    "y": [
        (
            "params",
            {
                "components": {
                    "tc1": {"url": "y_params_url", "version": "y_params_version"},
                }
            },
        ),
        ("m", {"components": {"tc4": {"url": "y_m_url"}}}),
        ("n", {"components": {"tc4": {"version": "y_n_version"}}}),
        ("o", {}),
    ],
    "z": [("a", {})],
}

# Generate a list of tuples (cloud, region) from the CLOUD_REGION_PARAMS map, this
# allows us to parametrize the cloud region reclass test in such a way that it only
# tests valid combinations of cloud and region.
CLOUD_REGION_TESTCASES = [
    (cloud, region[0])
    for cloud, regions in CLOUD_REGION_PARAMS.items()
    for region in regions
    if isinstance(regions, list)
    if region[0] != "params"
]


def setup_global_repo_dir(
    tmp_path: Path, global_params, distparams, cloud_region_params
) -> Path:
    global_path = tmp_path / "global-defaults"
    os.makedirs(global_path)
    os.makedirs(global_path / "distribution", exist_ok=True)
    os.makedirs(global_path / "cloud", exist_ok=True)
    ext = [".yml", ".yaml"]
    for distribution, params in distparams.items():
        # randomize extensions for distribution classes
        fext = random.choice(ext)
        yaml_dump(
            {"parameters": params},
            global_path / "distribution" / f"{distribution}{fext}",
        )
    for cloud, params in cloud_region_params.items():
        if isinstance(params, dict):
            yaml_dump({"parameters": params}, global_path / "cloud" / f"{cloud}.yml")
        else:
            assert isinstance(params, list)
            os.makedirs(global_path / "cloud" / cloud, exist_ok=True)
            rparams = {}
            for region, params in params:
                if region == "params":
                    rparams = params
                    continue
                yaml_dump(
                    {"parameters": params},
                    global_path / "cloud" / cloud / f"{region}.yml",
                )
            # Write cloud-level params
            yaml_dump(
                {"parameters": rparams},
                global_path / "cloud" / cloud / "params.yml",
            )
            # Configure cloud region hierarchy
            yaml_dump(
                {
                    "classes": [
                        f"global.cloud.{cloud}.params",
                        f"global.cloud.{cloud}.${{facts:region}}",
                    ],
                },
                global_path / "cloud" / f"{cloud}.yml",
            )

    # Write global params
    yaml_dump(
        {"parameters": global_params},
        global_path / "params.yml",
    )

    # Write hierarchy config
    yaml_dump(
        {
            "classes": [
                "global.params",
                "global.distribution.${facts:distribution}",
                "global.cloud.${facts:cloud}",
                "${cluster:tenant}.${cluster:name}",
            ]
        },
        global_path / "commodore.yml",
    )

    return global_path


def extract_cloud_region_params(cloud: str, region: str):
    cparams = None
    rparams = None
    crp = CLOUD_REGION_PARAMS[cloud]
    if isinstance(crp, dict):
        return crp, {}

    assert isinstance(crp, list)
    for cr, params in crp:
        if cr == region:
            rparams = params
        if cr == "params":
            cparams = params
    if not cparams:
        cparams = {}
    if not rparams:
        rparams = {}

    return cparams, rparams


def _extract_component(params: Dict, cn: str):
    return params.get("components", {}).get(cn, {})


def get_component(distribution: str, cloud: str, region: str, cn: str):
    if cloud:
        cparams, rparams = extract_cloud_region_params(cloud, region)
    else:
        cparams = {}
        rparams = {}

    if region:
        rc = _extract_component(rparams, cn)
    else:
        rc = {}

    if cloud:
        cc = _extract_component(cparams, cn)
    else:
        cc = {}

    if distribution:
        dparams = DIST_PARAMS[distribution]
        dc = _extract_component(dparams, cn)
    else:
        dc = {}

    curl = rc.get("url", cc.get("url", dc.get("url", cn)))
    cver = rc.get("version", cc.get("version", dc.get("version", "gp")))

    return {
        "url": curl,
        "version": cver,
    }


def verify_components(
    components: Dict[str, Dict[str, str]], distribution: str, cloud: str, region: str
):
    for cn, c in components.items():
        ec = get_component(distribution, cloud, region, cn)
        assert c["url"] == ec["url"]
        assert c["version"] == ec["version"]


def create_inventory_facts(
    tmp_path: Path,
    global_config: str,
    distribution: Optional[str],
    cloud: Optional[str],
    region: Optional[str],
    allow_missing_classes: Optional[bool] = True,
) -> parameters.InventoryFacts:
    params = {"parameters": {"facts": {}}}
    if distribution:
        params["parameters"]["facts"]["distribution"] = distribution
    if cloud:
        params["parameters"]["facts"]["cloud"] = cloud
    if region:
        params["parameters"]["facts"]["region"] = region

    values = tmp_path / "values.yaml"
    yaml_dump(params, values)

    return parameters.InventoryFacts(
        global_config, None, [values], allow_missing_classes
    )


def test_inventoryfactory_find_values(tmp_path: Path):
    distributions = {"a": {}, "b": {}, "c": {}, "d": {}}
    cloud_regions = {
        "x": {},
        "y": [("params", {}), ("m", {}), ("n", {}), ("o", {})],
        "z": [("a", {})],
    }
    expected_regions = {
        "x": [],
        "y": ["m", "n", "o"],
        "z": ["a"],
    }
    global_dir = setup_global_repo_dir(tmp_path, {}, distributions, cloud_regions)

    invfactory = parameters.InventoryFactory(work_dir=tmp_path, global_dir=global_dir)

    assert set(invfactory.distributions) == set(distributions.keys())
    assert set(invfactory.clouds) == set(cloud_regions.keys())
    for cloud in cloud_regions.keys():
        assert set(invfactory.cloud_regions[cloud]) == set(expected_regions[cloud])


def test_inventoryfactory_from_dir(tmp_path: Path):
    distributions = {"a": {}, "b": {}, "c": {}, "d": {}}
    cloud_regions = {
        "x": {},
        "y": [("params", {}), ("m", {}), ("n", {}), ("o", {})],
        "z": [("a", {})],
    }
    global_dir = setup_global_repo_dir(tmp_path, {}, distributions, cloud_regions)
    invfacts = create_inventory_facts(tmp_path, global_dir, None, None, None)

    invfactory = parameters.InventoryFactory.from_repo_dir(
        tmp_path, global_dir, invfacts
    )

    assert invfactory.classes_dir == (tmp_path / "inventory" / "classes")
    assert invfactory.targets_dir == (tmp_path / "inventory" / "targets")

    assert invfactory.classes_dir.exists()
    assert invfactory.classes_dir.is_dir()
    assert invfactory.targets_dir.exists()
    assert invfactory.targets_dir.is_dir()
    assert (invfactory.classes_dir / "global").exists()
    assert (invfactory.classes_dir / "global").is_symlink()


@pytest.mark.parametrize("distribution", ["a", "b", "c", "d"])
def test_inventoryfactory_reclass_distribution(tmp_path: Path, distribution: str):
    global_dir = setup_global_repo_dir(
        tmp_path, GLOBAL_PARAMS, DIST_PARAMS, CLOUD_REGION_PARAMS
    )
    invfacts = create_inventory_facts(tmp_path, global_dir, distribution, None, None)
    invfactory = parameters.InventoryFactory.from_repo_dir(
        tmp_path, global_dir, invfacts
    )

    inv = invfactory.reclass(invfacts)
    components = inv.parameters("components")

    assert set(components.keys()) == set(GLOBAL_PARAMS["components"].keys())
    verify_components(components, distribution, None, None)


@pytest.mark.parametrize("cloud", ["x", "y", "z"])
def test_inventoryfactory_reclass_cloud(tmp_path: Path, cloud: str):
    global_dir = setup_global_repo_dir(
        tmp_path, GLOBAL_PARAMS, DIST_PARAMS, CLOUD_REGION_PARAMS
    )
    invfacts = create_inventory_facts(tmp_path, global_dir, None, cloud, None)
    invfactory = parameters.InventoryFactory.from_repo_dir(
        tmp_path, global_dir, invfacts
    )

    inv = invfactory.reclass(invfacts)
    components = inv.parameters("components")

    assert set(components.keys()) == set(GLOBAL_PARAMS["components"].keys())
    verify_components(components, None, cloud, None)


@pytest.mark.parametrize("cloud,region", CLOUD_REGION_TESTCASES)
def test_inventoryfactory_reclass_cloud_region(tmp_path: Path, cloud: str, region: str):
    global_dir = setup_global_repo_dir(
        tmp_path, GLOBAL_PARAMS, DIST_PARAMS, CLOUD_REGION_PARAMS
    )
    invfacts = create_inventory_facts(tmp_path, global_dir, None, cloud, region)
    invfactory = parameters.InventoryFactory.from_repo_dir(
        tmp_path, global_dir, invfacts
    )

    inv = invfactory.reclass(invfacts)
    components = inv.parameters("components")

    assert set(components.keys()) == set(GLOBAL_PARAMS["components"].keys())
    verify_components(components, None, cloud, region)