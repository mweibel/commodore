import subprocess


def _setup_merge_conflict(tmp_path: Path):
    r, _ = setup_repo(tmp_path)
    test_txt_path = r.working_tree_dir / "test.txt"
    with open(test_txt_path, "w", encoding="utf-8") as f:
        f.write("Hello, world!\n")
    r.stage_all()
    r.commit("Add content to test.txt")
    assert not r.repo.is_dirty()

    diff = """diff --git a/test.txt b/test.txt
new file mode 100644
index 0000000..d56c457
--- /dev/null
+++ b/test.txt
@@ -0,0 +1 @@
+Yo World!
"""

    result = subprocess.run(
        ["git", "apply", "-3"], input=diff.encode(), cwd=r.working_tree_dir
    )
    # patch should not apply
    assert result.returncode == 1

    return r


def test_gitrepo_stage_all_raises_on_conflict(tmp_path: Path):
    r = _setup_merge_conflict(tmp_path)

    with pytest.raises(gitrepo.MergeConflict) as e:
        r.stage_all()

    assert str(e.value) == "test.txt"


def test_gitrepo_stage_files_raises_on_conflict(tmp_path: Path):
    r = _setup_merge_conflict(tmp_path)

    with pytest.raises(gitrepo.MergeConflict) as e:
        r.stage_files(["test.txt"])

    assert str(e.value) == "test.txt"