from __future__ import annotations

from pathlib import Path
from shutil import rmtree

import click
import git

from commodore.component import Component, component_dir
from commodore.dependency_templater import Templater
from commodore.multi_dependency import MultiDependency


class ComponentTemplater(Templater):
    library: bool
    post_process: bool
    matrix_tests: bool

    @property
    def cookiecutter_args(self) -> dict[str, str]:
        return {
            "add_lib": "y" if self.library else "n",
            "add_pp": "y" if self.post_process else "n",
            "add_golden": "y" if self.golden_tests else "n",
            "add_matrix": "y" if self.matrix_tests else "n",
            "copyright_holder": self.copyright_holder,
            "copyright_year": self.today.strftime("%Y"),
            "github_owner": self.github_owner,
            "name": self.name,
            "slug": self.slug,
        }

    @property
    def deptype(self) -> str:
        return "component"

    def dependency_dir(self) -> Path:
        return component_dir(self.config.work_dir, self.slug)

    def delete(self):
        cdir = component_dir(self.config.work_dir, self.slug)
        if cdir.exists():
            cr = git.Repo(cdir)
            cdep = MultiDependency(
                cr.remote().url, self.config.inventory.dependencies_dir
            )
            component = Component(
                self.slug, dependency=cdep, work_dir=self.config.work_dir
            )

            if not self.config.force:
                click.confirm(
                    "Are you sure you want to delete component "
                    f"{self.slug}? This action cannot be undone",
                    abort=True,
                )
            rmtree(component.target_directory)
            # We check for other checkouts here, because our MultiDependency doesn't
            # know if there's other dependencies which would be registered on it.
            if not cdep.has_checkouts():
                # Also delete bare copy of component repo, if there's no other
                # worktree checkouts for the same dependency repo.
                rmtree(cdep.repo_directory)
            else:
                click.echo(
                    f" > Not deleting bare copy of repository {cdep.url}. "
                    + "Other worktrees refer to the same reposiotry."
                )

            click.secho(f"Component {self.slug} successfully deleted 🎉", bold=True)
        else:
            raise click.BadParameter(
                "Cannot find component with slug " f"'{self.slug}'."
            )
