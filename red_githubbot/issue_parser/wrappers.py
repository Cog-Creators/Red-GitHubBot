from collections.abc import Iterable
from dataclasses import InitVar, dataclass, field
from typing import Optional


@dataclass
class ParsedIssueFragment:
    pass


@dataclass
class ParsedIssueRef(ParsedIssueFragment):
    slug: Optional[str]
    issue_number: int


@dataclass
class ParsedIssueAction(ParsedIssueRef):
    action: str


@dataclass
class ParsedIssueMention(ParsedIssueFragment):
    username: str


@dataclass
class ParsedIssue:
    action_names: InitVar[Iterable[str]]
    actions: dict[str, list[ParsedIssueAction]] = field(default_factory=dict)
    refs: list[ParsedIssueRef] = field(default_factory=list)
    refs_and_actions: list[ParsedIssueRef] = field(default_factory=list, repr=False)
    mentions: list[ParsedIssueMention] = field(default_factory=list)
    fragments: list[ParsedIssueFragment] = field(default_factory=list, repr=False)

    def __post_init__(self, action_names: tuple[str]) -> None:
        for action_name in action_names:
            self.actions.setdefault(action_name, [])
