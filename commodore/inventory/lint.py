import abc
from pathlib import Path
from typing import Any, Dict
from typing_extensions import Protocol

import click
import yaml

from commodore.config import Config
from commodore.helpers import yaml_load_all


from .lint_components import lint_component_versions
from .lint_deprecated_parameters import lint_deprecated_parameters


class LintFunc(Protocol):
    def __call__(self, file: Path, filecontents: Dict[str, Any]) -> int:
        ...


class Linter:
    @abc.abstractmethod
    def __call__(self, config: Config, path: Path) -> int:
        ...


class ComponentVersionLinter(Linter):
    def __call__(self, config: Config, path: Path) -> int:
        return run_linter(config, path, lint_component_versions)


class DeprecatedParameterLinter(Linter):
    def __call__(self, config: Config, path: Path) -> int:
        return run_linter(config, path, lint_deprecated_parameters)


LINTERS = {
    "component-versions": ComponentVersionLinter(),
    "deprecated-parameters": DeprecatedParameterLinter(),
}


def _lint_file(cfg: Config, file: Path, lintfunc: LintFunc) -> int:
    errcount = 0
    try:
        filecontents = yaml_load_all(file)
        if len(filecontents) == 0:
            if cfg.debug:
                click.echo(f"> Skipping empty file {file}")
        elif len(filecontents) > 1:
            if cfg.debug:
                click.echo(
                    f"> Skipping file {file}: Linting multi-document YAML streams is not supported",
                )
        elif not isinstance(filecontents[0], dict):
            if cfg.debug:
                click.echo(
                    f"> Skipping file {file}: Expected top-level dictionary in YAML document"
                )
        else:
            errcount = lintfunc(file, filecontents[0])

    except (yaml.YAMLError, UnicodeDecodeError) as e:
        if cfg.debug:
            click.echo(f"> Skipping file {file}: Unable to load as YAML: {e}")

    return errcount


def _lint_directory(cfg: Config, path: Path, lintfunc: LintFunc) -> int:
    if not path.is_dir():
        raise ValueError("Unexpected path argument: expected to be a directory")

    errcount = 0
    for dentry in path.iterdir():
        if dentry.stem.startswith("."):
            if cfg.debug:
                click.echo(f"> Skipping hidden directory entry {dentry}")
            continue
        if dentry.is_dir():
            errcount += _lint_directory(cfg, dentry, lintfunc)
        else:
            errcount += _lint_file(cfg, dentry, lintfunc)
    return errcount


def run_linter(cfg: Config, path: Path, lintfunc: LintFunc) -> int:
    """Run lint function `lintfunc` in `path`.

    If `path` is a directory, run the function in all `.ya?ml` files in the directory
    (recursively).
    If `path` is a file, run the lint function in that file, if it's a YAML file.

    Returns a value that can be used as exit code to indicate whether there were linting
    errors.
    """
    if path.is_dir():
        return _lint_directory(cfg, path, lintfunc)

    return _lint_file(cfg, path, lintfunc)