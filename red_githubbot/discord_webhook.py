"""
Discord webhook handling.

Custom handlers can be created by creating a function decorated with @_gh_router.register():

    @_gh_router.register("milestone", action="created")
    async def on_milestone_created(event: sansio.Event, *, webhook: Webhook) -> None:
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

from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from typing import Any

import discord
import mistune
import yarl
from aiohttp import web
from gidgethub import routing, sansio
from markdownify import markdownify
from mistune.renderers.markdown import MarkdownRenderer

from . import utils

_gh_router = routing.Router()
_GITHUB_AVATAR_URL = (
    "https://cdn.discordapp.com/avatars/354986384542662657/df91181b3f1cf0ef1592fbe18e0962d7.png"
)


class DiscordMarkdownRenderer(MarkdownRenderer):
    def __init__(self, max_length: int) -> None:
        self.__max_len = max_length
        self.__current_len = 0
        self.__last_token_ellipsis = False

    def iter_tokens(
        self, tokens: Iterable[dict[str, Any]], state: mistune.BlockState
    ) -> Iterable[str]:
        it1, it2 = itertools.tee(super().iter_tokens(tokens, state))
        if next(it2, None) is None:
            return

        max_len = self.__max_len
        ellipsis = "\n\N{HORIZONTAL ELLIPSIS}\n"
        ellipsis_len = len(ellipsis)
        for token, next_token in itertools.zip_longest(it1, it2):
            length_with_token = self.__current_len + len(token)
            if length_with_token > max_len:
                if not self.__last_token_ellipsis:
                    self.__last_token_ellipsis = True
                    self.__current_len += ellipsis_len
                    yield ellipsis
            elif length_with_token > max_len - ellipsis_len:
                if next_token is None:
                    self.__last_token_ellipsis = False
                    self.__current_len = length_with_token
                    yield token
                elif not self.__last_token_ellipsis:
                    self.__last_token_ellipsis = True
                    self.__current_len += ellipsis_len
                    yield ellipsis
            else:
                self.__last_token_ellipsis = False
                self.__current_len = length_with_token
                yield token

    def render_references(self, state: mistune.BlockState) -> Iterable[str]:
        yield from ()

    def link(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        if token.get("label"):
            return self.render_children(token, state)
        return super().link(token, state)

    def table(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        return "<a table was here>"


def render_gfm_to_discord(s: str, max_length: int) -> str:
    markdown = mistune.create_markdown(
        renderer=DiscordMarkdownRenderer(max_length),
        plugins=("strikethrough", "table", "task_lists"),
    )
    return markdown(markdownify(s, escape_asterisks=False, escape_underscores=False))


@_gh_router.register("pull_request", action="opened")
async def on_pull_request_opened(event: sansio.Event, *, webhook: Webhook) -> None:
    pr_data = event.data["pull_request"]

    embed = generate_basic_event_embed(event)
    embed.title = shorten_to(
        f"{embed.title}Pull request opened: #{pr_data['number']}: {pr_data['title']}", 256
    )
    embed.url = pr_data["html_url"]
    embed.description = render_gfm_to_discord(pr_data["body"], 4096)
    embed.color = discord.Color.from_rgb(0, 152, 0)
    if pr_data["draft"]:
        embed.add_field(name="Status", value="Draft")
    await webhook.send(embed=embed)


@_gh_router.register("pull_request", action="ready_for_review")
async def on_pull_request_ready_for_review(event: sansio.Event, *, webhook: Webhook) -> None:
    pr_data = event.data["pull_request"]

    embed = generate_basic_event_embed(event)
    embed.title = shorten_to(
        f"{embed.title}Pull request #{pr_data['number']} marked as ready for review", 256
    )
    embed.url = pr_data["html_url"]
    embed.color = discord.Color.from_rgb(0, 152, 0)
    await webhook.send(embed=embed)


@_gh_router.register("deployment_status")
async def on_deployment_status(event: sansio.Event, *, webhook: Webhook) -> None:
    status = event.data["deployment_status"]
    embed = generate_basic_event_embed(event)

    status_state = status["state"]
    if status_state == "error":
        status_text = "errored"
        embed.color = discord.Color.from_rgb(252, 41, 41)
    elif status_state == "failure":
        status_text = "failed"
        embed.color = discord.Color.from_rgb(252, 41, 41)
    elif status_state == "success":
        status_text = "succeeded"
        embed.color = discord.Color.from_rgb(0, 152, 0)
    else:
        return

    embed.title = shorten_to(
        f"{embed.title}Deployment {status_text}: {status['environment']}", 256
    )
    embed.url = status["target_url"]
    await webhook.send(embed=embed)


def shorten_to(text: str, max_length: int) -> str:
    if len(text) > max_length:
        return f"{text[: max_length - 1]}\N{HORIZONTAL ELLIPSIS}"
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

    actor = sender or (repo or {}).get("owner")
    if actor is not None:
        embed.set_author(name=actor["login"], url=actor["html_url"], icon_url=actor["avatar_url"])

    return embed


class Webhook(discord.Webhook):
    thread: discord.abc.Snowflake = discord.utils.MISSING

    async def send(
        self,
        content: str = discord.utils.MISSING,
        *,
        username: str = discord.utils.MISSING,
        avatar_url: Any = discord.utils.MISSING,
        file: discord.File = discord.utils.MISSING,
        files: Sequence[discord.File] = discord.utils.MISSING,
        embed: discord.Embed = discord.utils.MISSING,
        embeds: Sequence[discord.Embed] = discord.utils.MISSING,
        allowed_mentions: discord.AllowedMentions = discord.utils.MISSING,
        view: discord.ui.LayoutView | discord.ui.View = discord.utils.MISSING,
        thread_name: str = discord.utils.MISSING,
    ) -> None:
        if username is discord.utils.MISSING:
            username = "GitHub"
        if avatar_url is discord.utils.MISSING:
            avatar_url = _GITHUB_AVATAR_URL
        if allowed_mentions is discord.utils.MISSING:
            allowed_mentions = discord.AllowedMentions.none()
        else:
            allowed_mentions = discord.AllowedMentions.none().merge(allowed_mentions)
        await super().send(
            content,
            username=username,
            avatar_url=avatar_url,
            file=file,
            files=files,
            embed=embed,
            embeds=embeds,
            allowed_mentions=allowed_mentions,
            view=view,
            thread=self.thread,
            thread_name=thread_name,
        )


async def execute_default_github_webhook(event: sansio.Event, *, webhook: Webhook) -> web.Response:
    url = yarl.URL(f"{webhook.url}/github")
    if webhook.thread is not discord.utils.MISSING:
        url = url.update_query(thread_id=webhook.thread.id)
    async with utils.session.post(
        url, json=event.data, headers={"X-Github-Event": event.event}
    ) as resp:
        return web.Response(
            headers=resp.headers,
            status=resp.status,
            body=await resp.read(),
        )


async def handle_event(
    event: sansio.Event, *, webhook_id: str, webhook_token: str, thread_id: int | None = None
) -> web.Response:
    webhook = Webhook.partial(webhook_id, webhook_token, session=utils.session)
    if thread_id is not None:
        webhook.thread = discord.Object(thread_id)

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
