"""
Discord webhook handling.

Custom handlers can be created by creating a function decorated with @_gh_router.register():

    @_gh_router.register("milestone", action="created")
    async def on_milestone_created(event: sansio.Event, *, webhook: discord.Webhook) -> None:
        milestone = event.data["milestone"]
        embed = generate_basic_event_embed(event)
        embed.title += f"Milestone created: {milestone{['title']}"
        embed.url = milestone["url"]
        await webhook.send(embed=embed)

`action` kwarg to `@_gh_router.register()` is optional and can be used to further filter
the matched events by one of the top-level keys in the event payload.

`generate_basic_event_embed()` function does not need to be used but might be helpful
in setting some common properties for event embeds, see its docstring for more details.

`execute_default_github_webhook()` function can be called, if you want to use Discord webhook's
`/github` endpoint in your event handler. The default handler is only called automatically,
when no custom handlers are found that can handle the event (and its action, if any).
"""

import discord
from aiohttp import web
from gidgethub import routing, sansio

from . import utils

_gh_router = routing.Router()


@_gh_router.register("deployment_status")
async def on_deployment_status(event: sansio.Event, *, webhook: discord.Webhook) -> None:
    status = event.data["deployment_status"]
    status_state = status["state"]
    if status_state == "error":
        status_text = "errored"
    elif status_state == "failure":
        status_text = "failed"
    elif status_state == "success":
        status_text = "succeeded"
    else:
        return

    embed = generate_basic_event_embed(event)
    embed.title = shorten_to(
        f"{embed.title}Deployment {status_text}: {status['environment']}", 256
    )
    embed.url = status["target_url"]
    await webhook.send(embed=embed)


def shorten_to(text: str, max_length: int) -> str:
    if len(text) > max_length:
        return f"{text[:max_length-1]}\N{HORIZONTAL ELLIPSIS}"
    return text


def generate_basic_event_embed(event: sansio.Event) -> discord.Embed:
    """
    Generate an embed with common data from the event pre-filled.

    Specifically, this functions returns a `discord.Embed` with:
    - title set to "[repo_owner/repo_name] " which is a commonly wanted title prefix
    - author set to the event sender's name and URL or, if sender is not known, repo owner's
    """
    embed = discord.Embed()
    repo = event.data.get("repository")
    sender = event.data.get("sender")

    if repo is not None:
        embed.title = f"[{repo['full_name']}] "

    if sender is not None:
        embed.set_author(name=sender["login"], url=sender["html_url"])
    elif repo is not None:
        embed.set_author(name=repo["owner"]["name"], url=repo["owner"]["html_url"])

    return embed


async def execute_default_github_webhook(
    event: sansio.Event, *, webhook: discord.Webhook
) -> web.Response:
    async with utils.session.post(
        f"{webhook.url}/github", json=event.data, headers={"X-Github-Event": event.event}
    ) as resp:
        return web.Response(
            headers=resp.headers,
            status=resp.status,
            body=await resp.read(),
        )


async def handle_event(
    event: sansio.Event, *, webhook_id: str, webhook_token: str
) -> web.Response:
    webhook = discord.Webhook.partial(webhook_id, webhook_token, session=utils.session)
    found_callbacks = _gh_router.fetch(event)
    if found_callbacks:
        for callback in found_callbacks:
            await callback(event, webhook=webhook)
        return web.Response(status=200)

    # If no custom handler is configured and Discord supports the event per below list,
    # use Discord's default.
    # https://docs.discord.com/developers/resources/webhook#execute-github-compatible-webhook
    if event.event in (
        "commit_comment",
        "create",
        "delete",
        "fork",
        "issue_comment",
        "issues",
        "member",
        "public",
        "pull_request",
        "pull_request_review",
        "push",
        "release",
        "watch",
        "check_run",
        "check_suite",
        "discussion",
        "discussion_comment",
    ):
        return await execute_default_github_webhook(event, webhook=webhook)
