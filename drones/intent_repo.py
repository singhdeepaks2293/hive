"""drones/intent_repo.py -- where desired state lives.

IntentRepo is what the Intent drone reads and where requests become pull
requests. The same object answers the Queen's ApprovalSource question ("who
approved the change that produced this version?") because only the Git
provider can answer it truthfully; the Intent drone's word is not enough.

LocalIntentRepo: a directory of YAML files (intent/devices/*.yaml) with an
in-memory PR workflow and branch protection (one non-author approval to
merge). Good for the lab, tests and the demo.

GitHubIntentRepo: the production shape, using the GitHub REST API. Left as a
documented skeleton until it can be tested against a real repo and token.
"""
from __future__ import annotations

import copy
import itertools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import yaml

from queen.consent import PullRequestApproval
from queen.models import stable_hash


@dataclass
class PullRequest:
    id: int
    author: str
    title: str
    patch: dict[str, dict[str, Any]]           # device -> {key: value, or None to remove}
    state: str = "open"                        # open | merged | closed
    reviewers: list[str] = field(default_factory=list)
    merged_sha: Optional[str] = None

    @property
    def url(self) -> str:
        return f"pr/{self.id}"


class IntentRepo(Protocol):
    name: str
    def head(self) -> str: ...
    def source(self, sha: Optional[str] = None) -> str: ...
    def devices(self) -> dict[str, dict[str, Any]]: ...
    def open_pr(self, author: str, title: str, patch: dict[str, dict[str, Any]]) -> PullRequest: ...
    def approvals(self, source: str) -> list[PullRequestApproval]: ...


class LocalIntentRepo:
    def __init__(self, path: str | Path, name: str = "intent") -> None:
        self.name = name
        self._docs: dict[str, dict[str, Any]] = {}
        for f in sorted(Path(path).glob("*.yaml")):
            doc = yaml.safe_load(f.read_text())
            self._docs[doc["device"]] = doc
        if not self._docs:
            raise ValueError(f"no intent files in {path}")
        self._sha = self._hash()
        self._prs: dict[int, PullRequest] = {}
        self._ids = itertools.count(1)

    def _hash(self) -> str:
        return stable_hash(self._docs)[:7]

    def head(self) -> str:
        return self._sha

    def source(self, sha: Optional[str] = None) -> str:
        return f"git:{self.name}@{sha or self._sha}"

    def devices(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._docs)

    def pull_requests(self) -> list[PullRequest]:
        return list(self._prs.values())

    def open_pr(self, author: str, title: str, patch: dict[str, dict[str, Any]]) -> PullRequest:
        unknown = set(patch) - set(self._docs)
        if unknown:
            raise ValueError(f"no intent for {sorted(unknown)}")
        pr = PullRequest(id=next(self._ids), author=author, title=title, patch=patch)
        self._prs[pr.id] = pr
        return pr

    def approve(self, pr_id: int, reviewer: str) -> None:
        pr = self._prs[pr_id]
        if reviewer == pr.author:
            raise PermissionError("authors cannot approve their own pull request")
        if reviewer not in pr.reviewers:
            pr.reviewers.append(reviewer)

    def merge(self, pr_id: int) -> str:
        pr = self._prs[pr_id]
        if pr.state != "open":
            raise ValueError(f"pull request {pr_id} is {pr.state}")
        if not pr.reviewers:
            raise PermissionError("branch protection: one approval from someone else required")
        for device, changes in pr.patch.items():
            cfg = self._docs[device]["config"]
            for key, value in changes.items():
                if value is None:
                    cfg.pop(key, None)
                else:
                    cfg[key] = value
        self._sha = self._hash()
        pr.state, pr.merged_sha = "merged", self._sha
        return self._sha

    def approvals(self, source: str) -> list[PullRequestApproval]:
        if not source.startswith(f"git:{self.name}@"):
            return []
        sha = source.rsplit("@", 1)[1]
        return [PullRequestApproval(reviewer=r, author=pr.author, merged=True)
                for pr in self._prs.values() if pr.merged_sha == sha for r in pr.reviewers]


class GitHubIntentRepo:
    """Production intent source. Endpoints it needs (GitHub REST v3):

      head()        GET  /repos/{owner}/{repo}/commits/{branch}            -> sha
      devices()     GET  /repos/{owner}/{repo}/contents/{path}?ref={sha}    (+ each file)
      open_pr()     POST /repos/{owner}/{repo}/git/refs, PUT contents, POST /pulls
      approvals()   GET  /repos/{owner}/{repo}/commits/{sha}/pulls
                    GET  /repos/{owner}/{repo}/pulls/{n}/reviews         (state == APPROVED)
                    only merged PRs count; the PR author never counts

    Use a fine-grained token with contents:read, pull_requests:write on the
    intent repo only. Map GitHub logins to hive users in the authenticator.
    """

    def __init__(self, owner: str, repo: str, path: str = "devices", branch: str = "main",
                 token_env: str = "HIVE_GITHUB_TOKEN") -> None:
        self.name = f"{owner}/{repo}"
        self.owner, self.repo, self.path, self.branch = owner, repo, path, branch
        self.token_env = token_env

    def _todo(self) -> Any:
        raise NotImplementedError("GitHubIntentRepo is a skeleton; use LocalIntentRepo in the lab")

    def head(self) -> str:
        return self._todo()

    def source(self, sha: Optional[str] = None) -> str:
        return f"git:{self.name}@{sha or self.head()}"

    def devices(self) -> dict[str, dict[str, Any]]:
        return self._todo()

    def open_pr(self, author: str, title: str, patch: dict[str, dict[str, Any]]) -> PullRequest:
        return self._todo()

    def approvals(self, source: str) -> list[PullRequestApproval]:
        return self._todo()
