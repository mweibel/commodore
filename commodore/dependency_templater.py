from __future__ import annotations

import datetime
import json
import re
import tempfile
import shutil
import textwrap

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional, Sequence

import click

from commodore.config import Config
from commodore.cruft import create as cruft_create
from commodore.gitrepo import GitRepo
from commodore.multi_dependency import MultiDependency

SLUG_REGEX = re.compile("^[a-z][a-z0-9-]+[a-z0-9]$")


class Templater(ABC):
    config: Config
    _slug: str
    _name: Optional[str]
    github_owner: str
    copyright_holder: str
    golden_tests: bool
    today: datetime.date
    output_dir: Optional[Path] = None
    _target_dir: Optional[Path] = None
    template_url: str
    template_version: Optional[str] = None

    def __init__(
        self,
        config: Config,
        template_url: str,
        template_version: Optional[str],
        slug: str,
        name: Optional[str] = None,
        output_dir: str = "",
    ):
        self.config = config
        self.template_url = template_url
        self.template_version = template_version
        self.slug = slug
        self._name = name
        self.today = datetime.date.today()
        if output_dir != "":
            odir = Path(output_dir)
            if not odir.is_dir():
                raise click.ClickException(f"Output directory {odir} doesn't exist")

            self.output_dir = odir

    @property
    @abstractmethod
    def deptype(self) -> str:
        """Return dependency type of template as string.

        The base implementation of `_validate_slug()` will reject slugs which are
        prefixed with the value of this property.
        """

    @property
    @abstractmethod
    def cookiecutter_args(self) -> dict[str, str]:
        """Cookiecutter template inputs.

        Passed to the rendering function as `extra_context`
        """

    @abstractmethod
    def dependency_dir(self) -> Path:
        """Location of dependency in the Commodore working directory.

        Used by `target_dir()` if neither `_target_dir` nor `_output_dir` is set."""

    @property
    def target_dir(self) -> Path:
        """Return Path indicating where to render the template to."""
        if self._target_dir:
            return self._target_dir

        if self.output_dir:
            return self.output_dir / self.slug

        return self.dependency_dir()

    def _validate_slug(self, value: str) -> str:
        if value.startswith(f"{self.deptype}-"):
            raise click.ClickException(
                f"The {self.deptype} slug may not start with '{self.deptype}-'"
            )
        if not SLUG_REGEX.match(value):
            raise click.ClickException(
                f"The {self.deptype} slug must match '{SLUG_REGEX.pattern}'"
            )
        return value

    @property
    def slug(self) -> str:
        return self._slug

    @slug.setter
    def slug(self, value: str):
        self._slug = self._validate_slug(value)

    @property
    def name(self) -> str:
        if not self._name:
            return self.slug
        return self._name

    @property
    def repo_url(self) -> str:
        return f"git@github.com:{self.github_owner}/{self.deptype}-{self.slug}.git"

    @property
    def additional_files(self) -> Sequence[str]:
        return [
            ".github",
            ".gitignore",
            ".*.yml",
            ".editorconfig",
            ".cruft.json",
        ]

    @property
    def template_commit(self) -> Optional[str]:
        cruft_json = self.target_dir / ".cruft.json"
        if not cruft_json.is_file():
            click.echo(
                f" > {self.deptype.capitalize()} doesn't have a `.cruft.json`, "
                + "can't determine template commit."
            )
            return None

        with open(cruft_json, "r", encoding="utf-8") as f:
            cruft_json_data = json.load(f)
            return cruft_json_data["commit"]

    def create(self) -> None:
        click.secho(f"Adding {self.deptype} {self.name}...", bold=True)

        if self.target_dir.exists():
            raise click.ClickException(
                f"Unable to add {self.deptype} {self.name}: "
                + f"{self.target_dir} already exists."
            )

        want_worktree = (
            self.config.inventory.dependencies_dir in self.target_dir.parents
        )
        if want_worktree:
            md = MultiDependency(self.repo_url, self.config.inventory.dependencies_dir)
            md.initialize_worktree(self.target_dir)

        with tempfile.TemporaryDirectory() as tmpdir:
            cruft_create(
                self.template_url,
                checkout=self.template_version,
                extra_context=self.cookiecutter_args,
                no_input=True,
                output_dir=Path(tmpdir),
            )
            shutil.copytree(
                Path(tmpdir) / self.slug, self.target_dir, dirs_exist_ok=True
            )

        self.commit("Initial commit", amend=want_worktree)
        click.secho(
            f"{self.deptype.capitalize()} {self.name} successfully added 🎉", bold=True
        )

    def commit(self, msg: str, amend=False, init=True) -> bool:
        # If we're amending an existing commit, we don't want to force initialize the
        # repo.
        repo = GitRepo(self.repo_url, self.target_dir, force_init=not amend and init)

        # stage_all() returns the full diff compared to the last commit. Therefore, we
        # do stage_files() first and then stage_all(), to ensure we get the complete
        # diff.
        repo.stage_files(self.additional_files)
        diff_text, changed = repo.stage_all()

        if changed:
            indented = textwrap.indent(diff_text, "     ")
            message = f" > Changes:\n{indented}"
        else:
            message = " > No changes."
        click.echo(message)

        if changed:
            # Only create a new commit if there are any changes.
            repo.commit(msg, amend=amend)
        return changed
