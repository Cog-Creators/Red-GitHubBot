import regex

from .actions import KEYWORDS

# partial regex pattern strings

_KEYWORD_PATTERN = rf"""
(?:
    (?P<keyword_name>{'|'.join(map(regex.escape, KEYWORDS))})

    # the issue reference needs to be delimited from the keyword by
    # any amount of whitespace and optionally one colon in it
    (?:\ |\t)*
    (?:\ |\t|:)
    (?:\ |\t)*
)
"""
_AUTO_REF_PATTERN = rf"""
# match (optional) keyword
{_KEYWORD_PATTERN}?

# match issue prefix
(?:
    # match GitHub issue/pull URL
    #
    # domain is optional on purpose as GitHub autolinks without the domain too
    (?:https://github\.com/)?
    (?P<slug>[\w\-\.]+/[\w\-\.]+)/
    (?:issues|pull)/

    |

    # match autolinked reference with an optional repo name
    (?P<slug>[\w\-\.]+/[\w\-\.]+)?
    (?:\#|gh-)
)
# match issue number
(?P<issue_number>\d+)

# ensure the match doesn't end with a word character
(?!\w)
"""
_MENTION_PATTERN = r"@(?P<mention>[\w\-\.]+)"

# Pattern objects

TEXT_RE = regex.compile(
    rf"""
    # ensure the match doesn't start with a word character
    (?:[^\w\n\v\r]|^)
    (?:
        {_AUTO_REF_PATTERN}
        |
        {_MENTION_PATTERN}
    )
    """,
    regex.IGNORECASE | regex.MULTILINE | regex.VERBOSE,
)
KEYWORD_RE = regex.compile(
    rf"""
    # ensure the match doesn't start with a word character
    (?:[^\w\n\v\r]|^)
    {_KEYWORD_PATTERN}
    $
    """,
    regex.IGNORECASE | regex.VERBOSE,
)
ISSUE_URL_RE = regex.compile(
    r"""
    ^
    (?:https://github\.com/)
    (?P<slug>[\w\-\.]+/[\w\-\.]+)/
    (?:issues|pull)/
    (?P<issue_number>\d+)
    (?!\w)
    """,
    regex.IGNORECASE | regex.VERBOSE,
)
