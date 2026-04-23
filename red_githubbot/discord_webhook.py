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

import itertools
from collections.abc import Iterable, Sequence
from typing import Any, Self

import discord
import mistune
import yarl
from aiohttp import web
from bs4 import NavigableString
from gidgethub import routing, sansio
from markdownify import MarkdownConverter
from mistune.renderers.markdown import MarkdownRenderer

from . import utils

_gh_router = routing.Router()
_GITHUB_AVATAR_URL = (
    "https://cdn.discordapp.com/avatars/354986384542662657/df91181b3f1cf0ef1592fbe18e0962d7.png"
)
_GFM_PLUGINS = ("strikethrough", "table", "task_lists")


class DiscordMarkdownRenderer(MarkdownRenderer):
    def __init__(
        self,
        max_length: int,
        *,
        base_component_count: int = 0,
        max_component_count: int = 40,
    ) -> None:
        self.__max_len = max_length
        self.__base_component_count = base_component_count
        self.__max_component_count = max_component_count
        self.__current_len = 0
        self.__last_token_ellipsis = False
        self._images: list[str] = []

    def iter_ui_items(
        self, tokens: Iterable[dict[str, Any]], state: mistune.BlockState
    ) -> Iterable[discord.ui.Item]:
        last_images_length = 0
        ellipsis = "\n\N{HORIZONTAL ELLIPSIS}\n"
        pending_tokens = []
        component_count = self.__base_component_count
        max_component_count = self.__max_component_count

        for token in self.iter_tokens(tokens, state, _ignore_images=True):
            images_length = len(self._images)
            if images_length == last_images_length:
                pending_tokens.append(token)
                continue
            if component_count == (max_component_count - 1):
                yield discord.ui.TextDisplay(ellipsis)
                return

            if pending_tokens:
                yield discord.ui.TextDisplay("".join(pending_tokens))
                component_count += 1

            images = self._images[last_images_length:]
            if len(images) > 1:
                gallery = discord.ui.MediaGallery()
                for image_url in images[:10]:
                    gallery.add_item(image_url)
                yield gallery
                component_count += 1
            else:
                yield discord.ui.Section(
                    discord.ui.TextDisplay("\u200b"), accessory=discord.ui.Thumbnail(image_url)
                )
                component_count += 3
            last_images_length = images_length

        if pending_tokens:
            yield discord.ui.TextDisplay("".join(pending_tokens))

    def iter_tokens(
        self,
        tokens: Iterable[dict[str, Any]],
        state: mistune.BlockState,
        *,
        _ignore_images: bool = False,
    ) -> Iterable[str]:
        it1, it2 = itertools.tee(super().iter_tokens(tokens, state))
        if next(it2, None) is None:
            return

        max_len = self.__max_len
        ellipsis = "\n\N{HORIZONTAL ELLIPSIS}\n"
        ellipsis_len = len(ellipsis)
        last_images_length = 0
        for token, next_token in itertools.zip_longest(it1, it2):
            images_length = len(self._images)
            if _ignore_images and images_length > last_images_length:
                last_images_length = images_length
                token = "\u200b"
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

    def image(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        if token.get("label"):
            return self.render_children(token, state)
        self._images.append(token["attrs"]["url"])
        return super().image(token, state)

    def link(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        if token.get("label"):
            return self.render_children(token, state)
        return super().link(token, state)

    def table(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        return "<a table was here>"

    def strikethrough(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        return f"~~{self.render_children(token, state)}~~"

    def thematic_break(self, token: dict[str, Any], state: mistune.BlockState) -> str:
        return "---\n\n"


class WhitespacePreservingMarkdownConverter(MarkdownConverter):
    def process_text(self, el: NavigableString, parent_tags: set[str] | None = None) -> str:
        parent_tags = set(parent_tags or ())
        # process_text() skips whitespace normalization inside a preformatted element,
        # let's use that!
        parent_tags.add("pre")
        return super().process_text(el, parent_tags)


class MarkdownContainer(discord.ui.Container):
    _title_component = discord.ui.TextDisplay("")

    def __init__(self) -> None:
        super().__init__()
        self._title = ""
        self._url = ""

    def _update_title_component(self) -> None:
        if not self._title:
            self._title_component.content = ""
            return
        text = f"[{self._title}]({self._url})" if self._url else self._title
        self._title_component = f"### {text}"

    @property
    def title(self) -> str:
        return self._title

    @title.setter
    def title(self, value: str) -> None:
        self._title = value
        self._update_title_component()

    @property
    def url(self) -> str:
        return self._url

    @url.setter
    def url(self, value: str) -> None:
        self._url = value
        self._update_title_component()


class MarkdownView(discord.ui.LayoutView):
    _container = MarkdownContainer()

    def __init__(
        self,
        *,
        title: str = "",
        url: str = "",
        color: discord.Color | int | None = None,
    ) -> None:
        self.title = title
        self.url = url
        self.color = color
        super().__init__(timeout=None)

    def add_container_item(self, item: discord.ui.Item[Any]) -> Self:
        self._container.add_item(item)
        return self

    @property
    def title(self) -> str:
        return self._container.title

    @title.setter
    def title(self, value: str) -> None:
        self._container.title = value

    @property
    def url(self) -> str:
        return self._container.url

    @url.setter
    def url(self, value: str) -> None:
        self._container.url = value

    @property
    def color(self) -> discord.Color | int | None:
        return self._container.accent_color

    @color.setter
    def color(self, value: discord.Color | int | None) -> None:
        self._container.accent_color = value


def convert_gfm_html_to_md(s: str) -> str:
    converter = WhitespacePreservingMarkdownConverter(
        escape_asterisks=False, escape_underscores=False
    )
    return converter.convert(s)


def render_gfm_to_discord(s: str, max_length: int) -> str:
    markdown = mistune.create_markdown(
        renderer=DiscordMarkdownRenderer(max_length), plugins=_GFM_PLUGINS
    )
    return markdown(convert_gfm_html_to_md(s))


def render_gfm_to_discord_components(
    s: str,
    *,
    max_length: int,
    base_component_count: int = 0,
    max_component_count: int = 40,
) -> Iterable[str]:
    markdown = mistune.create_markdown(renderer=None, plugins=_GFM_PLUGINS)
    tokens, state = markdown.parse(convert_gfm_html_to_md(s))

    renderer = DiscordMarkdownRenderer(
        max_length,
        base_component_count=base_component_count,
        max_component_count=max_component_count,
    )
    yield from renderer.iter_ui_items(tokens, state)


@_gh_router.register("pull_request", action="opened")
async def on_pull_request_opened(event: sansio.Event, *, webhook: Webhook) -> None:
    if utils.FEATURE_FLAGS.render_prs_with_components:
        await _on_pull_request_opened_components(event, webhook=webhook)
    else:
        await _on_pull_request_opened_embed(event, webhook=webhook)


async def _on_pull_request_opened_embed(event: sansio.Event, *, webhook: Webhook) -> None:
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


async def _on_pull_request_opened_components(event: sansio.Event, *, webhook: Webhook) -> None:
    pr_data = event.data["pull_request"]

    embed = generate_basic_event_embed(event)
    view = MarkdownView(
        title=shorten_to(
            f"{embed.title}Pull request opened: #{pr_data['number']}: {pr_data['title']}", 256
        ),
        url=pr_data["html_url"],
        color=discord.Color.from_rgb(0, 152, 0),
    )
    footer = discord.TextDisplay()
    if pr_data["draft"]:
        footer.content = "-# This PR is a draft."

    for item in render_gfm_to_discord_components(
        pr_data["body"],
        max_length=4000 - view.content_length() - len(footer.content),
        base_component_count=view.total_children_count,
        max_component_count=40 - bool(footer.content),
    ):
        view.add_container_item(item)

    if footer.content:
        view.add_container_item(footer)

    await webhook.send(view=view)


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
