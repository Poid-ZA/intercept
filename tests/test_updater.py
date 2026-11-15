"""Tests for the GitHub update check."""

import io
import json
import time

import pytest

from utils import updater


class _Response(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def github(monkeypatch):
    """Fake GitHub API: set `github['release']` / `github['tags']`; None means the call fails."""
    state = {"release": None, "tags": None}
    settings = {}

    def fake_urlopen(req, timeout=10):
        url = req.full_url
        payload = state["release"] if url.endswith("/releases/latest") else state["tags"]
        if payload is None:
            raise updater.URLError("offline")
        return _Response(json.dumps(payload).encode())

    monkeypatch.setattr(updater, "urlopen", fake_urlopen)
    monkeypatch.setattr(updater, "get_setting", settings.get)
    monkeypatch.setattr(updater, "set_setting", settings.__setitem__)
    monkeypatch.setattr(updater.config, "VERSION", "2.33.66", raising=False)
    monkeypatch.setattr(updater.config, "UPDATE_CHECK_ENABLED", True, raising=False)
    state["settings"] = settings
    return state


def _release(tag):
    return {"tag_name": tag, "html_url": f"https://example/{tag}", "body": "notes", "published_at": "", "name": tag}


def test_newer_tag_beats_stale_release(github):
    # The last GitHub release is older than this install, but newer versions were tagged
    github["release"] = _release("v2.33.47")
    github["tags"] = [{"name": "v2.33.73"}, {"name": "v2.33.75"}, {"name": "v2.33.9"}, {"name": "not-a-version"}]
    result = updater.check_for_updates(force=True)
    assert result["latest_version"] == "2.33.75"
    assert result["update_available"] is True
    assert result["release_url"].endswith("/releases/tag/v2.33.75")


def test_release_used_when_it_is_newest(github):
    github["release"] = _release("v2.34.0")
    github["tags"] = [{"name": "v2.33.75"}]
    result = updater.check_for_updates(force=True)
    assert result["latest_version"] == "2.34.0"
    assert result["release_notes"] == "notes"


def test_tags_alone_work_when_releases_fail(github):
    github["tags"] = [{"name": "v2.33.70"}]
    assert updater.check_for_updates(force=True)["latest_version"] == "2.33.70"


def test_stale_cached_latest_is_not_shown(github):
    # Cache written by an older build that only looked at releases/latest
    github["settings"].update(
        {updater.CACHE_KEY_LATEST_VERSION: "2.33.47", updater.CACHE_KEY_LAST_CHECK: str(time.time())}
    )
    assert updater.get_update_status()["checked"] is False
    # A normal (non-forced) check ignores the stale cache and asks GitHub again
    github["tags"] = [{"name": "v2.33.75"}]
    result = updater.check_for_updates()
    assert result["latest_version"] == "2.33.75"
    assert result["cached"] is False


def _git(repo, *args):
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def test_upstream_remote_follows_tracking_branch(tmp_path):
    """A feature branch tracking `fork` must fetch and pull from `fork`, not `origin`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "feat/x")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "c")
    _git(repo, "remote", "add", "fork", "https://example.invalid/fork.git")
    _git(repo, "update-ref", "refs/remotes/fork/feat/x", "HEAD")
    _git(repo, "config", "branch.feat/x.remote", "fork")
    _git(repo, "config", "branch.feat/x.merge", "refs/heads/feat/x")

    assert updater._upstream_remote_and_branch(str(repo), "feat/x") == ("fork", "feat/x")


def test_upstream_defaults_to_origin_without_tracking(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")

    assert updater._upstream_remote_and_branch(str(repo), "main") == ("origin", "main")


def test_git_runs_as_repo_owner_when_root(tmp_path, monkeypatch):
    """Root must run git as the repo owner, or the update leaves root-owned files in .git."""
    seen = {}
    monkeypatch.setattr(updater.os, "geteuid", lambda: 0)
    monkeypatch.setattr(updater.subprocess, "run", lambda cmd, **kw: seen.update(kw) or "ran")

    assert updater._git_run(["git", "status"], cwd=str(tmp_path), capture_output=True) == "ran"

    st = tmp_path.stat()
    assert seen["user"] == st.st_uid
    assert seen["group"] == st.st_gid
    assert seen["env"]["HOME"]
