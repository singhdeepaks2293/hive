import pytest

from queen import ChangeState, ConsentError, PullRequestApproval
from queen.consent import content_hash

from .helpers import join, make_queen, run, work


async def propose(q, auth, *, device="ie-sw2", lines=("ntp server 10.0.0.1",),
                  source="test:1", requester="manish", verify=True, passed=True):
    await join(q, "intent", "config.propose")
    await join(q, "guardian", "verify.change")
    await q.request(auth.issue(requester), "configure ntp", device=device)
    await work(q, "intent", {"proposal": {"device": device, "config": list(lines),
                                          "source": source}})
    (change,) = await q.changes()
    if verify:
        await work(q, "guardian", {"passed": passed, "content_hash": change.content_hash,
                                   "evidence": {"problems": [] if passed else ["bad"]}})
        await q.tick()
    return (await q.changes())[0]


def test_verified_change_needs_an_approver_with_the_role():
    async def go():
        q, board, clock, auth = make_queen()
        c = await propose(q, auth)
        assert c.state is ChangeState.AWAITING_APPROVAL
        with pytest.raises(ConsentError, match="lacks role"):
            await q.vote(c.id, auth.issue("intern"), "approve", c.content_hash)
        with pytest.raises(ConsentError, match="unauthenticated"):
            await q.vote(c.id, "deepak.0000", "approve", c.content_hash)
        c = await q.vote(c.id, auth.issue("deepak"), "approve", c.content_hash)
        assert c.state is ChangeState.APPROVED
    run(go())


def test_requester_cannot_approve_their_own_change():
    async def go():
        q, board, clock, auth = make_queen()
        c = await propose(q, auth, requester="manish")
        with pytest.raises(ConsentError, match="cannot approve"):
            await q.vote(c.id, auth.issue("manish"), "approve", c.content_hash)
    run(go())


def test_a_vote_is_bound_to_the_exact_content():
    async def go():
        q, board, clock, auth = make_queen()
        c = await propose(q, auth)
        other = content_hash(c.device, "ntp server 6.6.6.6", c.source)
        with pytest.raises(ConsentError, match="different content"):
            await q.vote(c.id, auth.issue("deepak"), "approve", other)
    run(go())


def test_approval_before_verification_waits_and_failed_verification_rejects():
    async def go():
        q, board, clock, auth = make_queen()
        c = await propose(q, auth, verify=False)
        c = await q.vote(c.id, auth.issue("deepak"), "approve", c.content_hash)
        assert c.state is ChangeState.VERIFYING                 # vote kept, not yet effective
        await work(q, "guardian", {"passed": False, "content_hash": c.content_hash,
                                   "evidence": {"problems": ["pre-condition rep"]}})
        assert (await q.changes())[0].state is ChangeState.REJECTED
    run(go())


def test_guardian_verdict_must_name_the_change_it_verified():
    async def go():
        q, board, clock, auth = make_queen()
        await propose(q, auth, verify=False)
        await work(q, "guardian", {"passed": True, "content_hash": "something-else"})
        assert (await q.changes())[0].state is ChangeState.REJECTED
    run(go())


def test_any_reject_vetoes():
    async def go():
        q, board, clock, auth = make_queen()
        users = {"manish": {"netops-approver"}, "deepak": {"netops-approver"},
                 "ravi": {"netops-approver"}}
        from queen import HmacAuthenticator
        q.auth = q.gate.auth = HmacAuthenticator(b"test-secret-0123456789", users)
        c = await propose(q, q.auth, requester="manish")
        await q.vote(c.id, q.auth.issue("ravi"), "reject", c.content_hash, "not in window")
        assert (await q.changes())[0].state is ChangeState.REJECTED
    run(go())


class FakeGit:
    def __init__(self, *approvals):
        self._a = list(approvals)

    def approvals(self, source):
        return self._a


def test_merged_pr_review_counts_but_not_the_author_or_unmerged():
    async def go():
        git = FakeGit(PullRequestApproval(reviewer="manish", author="manish", merged=True),
                      PullRequestApproval(reviewer="intern", author="manish", merged=True),
                      PullRequestApproval(reviewer="deepak", author="manish", merged=False))
        q, board, clock, auth = make_queen(approvals=git)
        await join(q, "intent", "drift.remediate")
        await propose(q, auth, source="git:intent@abc", requester="manish")
        await q.tick()
        assert (await q.changes())[0].state is ChangeState.AWAITING_APPROVAL
        git._a.append(PullRequestApproval(reviewer="deepak", author="manish", merged=True))
        await q.tick()
        c = (await q.changes())[0]
        assert c.state in (ChangeState.APPROVED, ChangeState.APPLYING)
        assert [v.principal for v in c.votes] == ["git:deepak"]
    run(go())
