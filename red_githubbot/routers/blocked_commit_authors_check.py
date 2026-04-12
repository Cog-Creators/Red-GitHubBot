from gidgethub import sansio

from .. import utils
from . import gh_router

ALLOWED_BOT_EMAILS = {
    # Automated commits generated with GitHub Actions
    "41898282+github-actions[bot]@users.noreply.github.com",
}
BLOCKED_EMAILS = {
    ## Block AI agent advertising through "Co-authored-by"
    # Aider
    "aider@aider.chat",
    # Amp Code
    "amp@ampcode.com",
    # Anthropic - Claude, Codex, etc.
    "assistant@anthropic.com",
    "claude@anthropic.com",
    "noreply@anthropic.com",
    # Crush
    "crush@charm.land",
    # Cursor
    "cursoragent@cursor.com",
    # Google - Gemini, Jules, etc.
    "gemini@google.com",
    "noreply@google.com",
    # Letta Code
    "noreply@letta.com",
    # Microsoft
    "240397093+microsoft-amplifier@users.noreply.github.com",
    # Mistral Vibe
    "vibe@mistral.ai",
    # Ona
    "no-reply@ona.com",
    # OpenAI - Codex, GPT, etc.
    "codex@openai.com",
    "noreply@openai.com",
    # OpenCode
    "noreply@opencode.ai",
    # OpenHands
    "openhands@all-hands.dev",
    # Alibaba - Aone Copilot, Qwen, etc.
    "noreply@alibaba-inc.com",
    "qwen-coder@alibabacloud.com",
    # Roo Code
    "roomote@roocode.com",
}
CHECK_RUN_NAME = "Check for blocked commit authors"


def _is_user_blocked(name: str, email: str) -> bool:
    if email in BLOCKED_EMAILS:
        return True
    if email not in ALLOWED_BOT_EMAILS and (
        name.endswith("[bot]") or email.endswith("[bot]@users.noreply.github.com")
    ):
        return True
    return False


@gh_router.register("pull_request", action="opened")
@gh_router.register("pull_request", action="reopened")
@gh_router.register("pull_request", action="synchronize")
@gh_router.register("check_run", action="rerequested")
@gh_router.register("merge_group", action="check_requested")
async def blocked_commit_authors_check(event: sansio.Event) -> None:
    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)
    pr_data, head_sha = await utils.get_pr_data_for_check_run(
        gh, event=event, check_run_name=CHECK_RUN_NAME, get_pr_data=True
    )
    if pr_data is None:
        return

    found_blocked_users = set()
    found_blocked_commits = set()
    async for commit in gh.getiter(event.data["pull_request"]["commits_url"]):
        commit_data = commit["commit"]
        author = commit_data["author"]
        name = author["name"]
        email = author["email"]
        if _is_user_blocked(name, email):
            found_blocked_users.add(f"{name} <{email}>")
            found_blocked_commits.add(commit_data["sha"])

        for name, email in utils.get_coauthors_from_message(commit_data["message"]):
            if _is_user_blocked(name, email):
                found_blocked_users.add(f"{name} <{email}>")
                found_blocked_commits.add(commit_data["sha"])

    if found_blocked_users:
        conclusion = utils.CheckRunConclusion.FAILURE
        blocked_users = "\n".join(found_blocked_users)
        summary = (
            "The following blocked users were found to be authors on some of the commits:\n"
            f"```\n{blocked_users}\n```"
        )
        blocked_commits = "\n".join(found_blocked_commits)
        text = f"{summary}\nThe following commits are affected:\n```\n{blocked_commits}\n```"
        output = utils.CheckRunOutput(
            title="Found commits with blocked authors or co-authors.",
            summary=summary,
            text=text,
        )
    else:
        conclusion = utils.CheckRunConclusion.SUCCESS
        output = utils.CheckRunOutput(
            title="No blocked users were found.",
            summary="None of the commits in the PR are authored by blocked users.",
        )

    await utils.post_check_run(
        gh, name=CHECK_RUN_NAME, head_sha=head_sha, conclusion=conclusion, output=output
    )
