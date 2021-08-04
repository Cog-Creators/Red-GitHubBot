ACTIONS = {
    "close": (
        "close",
        "closes",
        "closed",
        "fix",
        "fixes",
        "fixed",
        "resolve",
        "resolves",
        "resolved",
    ),
}
KEYWORDS = {
    keyword: action for action, keyword_list in ACTIONS.items() for keyword in keyword_list
}
