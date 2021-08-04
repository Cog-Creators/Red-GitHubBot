from typing import Any, Optional

import regex

from ..utils import parse_markdown
from ._regexes import ISSUE_URL_RE, KEYWORD_RE, TEXT_RE
from .actions import ACTIONS, KEYWORDS
from .wrappers import ParsedIssue, ParsedIssueAction, ParsedIssueMention, ParsedIssueRef


def parse_issue_body(body: str) -> ParsedIssue:
    issue = ParsedIssue(ACTIONS.keys())
    _parse_children(issue, parse_markdown(body))
    return issue


def _parse_children(issue: ParsedIssue, nodes: list[dict[str, Any]]) -> None:
    for idx, node in enumerate(nodes):
        node_type = node["type"]
        if node_type in ("codespan", "inline_html", "block_code", "block_html"):
            continue

        if node_type == "link":
            _parse_link(issue, previous_node=nodes[idx] if idx else None, node=node)
            continue

        if text := node.get("text"):
            _parse_text(issue, text)

        if children := node.get("children"):
            _parse_children(issue, children)


def _parse_text(issue: ParsedIssue, text: str) -> None:
    for match in TEXT_RE.finditer(text):
        username = match.group("mention")
        if username is not None:
            fragment = ParsedIssueMention(username=username)
            issue.fragments.append(fragment)
            issue.mentions.append(fragment)
            continue

        _append_parsed_ref(issue, match=match, keyword_name=match.group("keyword_name"))


def _parse_link(
    issue: ParsedIssue, *, previous_node: Optional[dict[str, Any]], node: dict[str, Any]
) -> None:
    match = ISSUE_URL_RE.match(node["link"])
    if match is None:
        return

    keyword_name = None
    if previous_node is not None and previous_node["type"] == "text":
        keyword_match = KEYWORD_RE.search(previous_node["text"])
        keyword_name = keyword_match and keyword_match.group("keyword_name")

    _append_parsed_ref(issue, match=match, keyword_name=keyword_name)


def _append_parsed_ref(
    issue: ParsedIssue, *, match: regex.Match[str], keyword_name: Optional[str]
) -> None:
    issue_number = int(match.group("issue_number"))
    slug = match.group("slug")

    if keyword_name is None:
        ref = ParsedIssueRef(slug=slug, issue_number=issue_number)
        issue.refs.append(ref)
    else:
        action = KEYWORDS[keyword_name.lower()]
        ref = ParsedIssueAction(slug=slug, issue_number=issue_number, action=action)
        issue.actions[action].append(ref)

    issue.fragments.append(ref)
    issue.refs_and_actions.append(ref)
