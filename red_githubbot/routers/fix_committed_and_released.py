import json
import logging
from typing import Any

from gidgethub import sansio

from .. import utils
from ..constants import REPO_NAME, UPSTREAM_REPO, UPSTREAM_USERNAME
from . import gh_router

log = logging.getLogger(__name__)

GET_CLOSED_EVENT_QUERY = """
query getClosedEvent($owner: String! $name: String! $issue_number: Int!) {
  repository(owner: $owner name: $name) {
    issue(number: $issue_number) {
      timelineItems(itemTypes: [CLOSED_EVENT] last: 1) {
        nodes {
          ... on ClosedEvent {
            closer {
              __typename
            }
          }
        }
      }
    }
  }
}
""".strip()
GET_CLOSING_ISSUE_REFERENCES_QUERY = """
query getClosingIssueReferences($owner: String! $name: String! $pr_number: Int!) {
  repository(owner: $owner name: $name) {
    pullRequest(number: $pr_number) {
      closingIssuesReferences(last: 10) {
        nodes {
          number
          closed
          labels(last: 100) {
            nodes {
              name
            }
          }
        }
      }
    }
  }
}
""".strip()
GET_PR_HISTORY_QUERY = """
query getPRHistory(
  $owner: String!
  $name: String!
  $tag_name: String!
  $after: String
  $since: GitTimestamp!
) {
  repository(owner: $owner name: $name) {
    ref(qualifiedName: $tag_name) {
      target {
        ... on Commit {
          # querying 99 costs 10 while querying 100 costs 12
          history(first: 99 after: $after since: $since) {
            nodes {
              associatedPullRequests(first: 1) {
                nodes {
                  number
                  closingIssuesReferences(last: 10) {
                    nodes {
                      id
                      number
                      closed
                      labels(last: 100) {
                        nodes {
                          name
                        }
                      }
                    }
                  }
                }
              }
            }
            pageInfo {
              hasNextPage
              endCursor
            }
          }
        }
      }
    }
  }
}
""".strip()
ADD_AND_REMOVE_LABELS_MUTATION_TMPL = """
add%(id)s: addLabelsToLabelable(
  input: {
    labelIds: %(labels_to_add)s
    labelableId: %(labelable_id)s
  }
) {
  clientMutationId
}
remove%(id)s: removeLabelsFromLabelable(
  input: {
    labelIds: %(labels_to_remove)s
    labelableId: %(labelable_id)s
  }
) {
  clientMutationId
}
""".strip()

latest_event_id: int = 0


@gh_router.register("issue", action="closed")
async def apply_resolution_if_closed_by_pr_or_commit(event: sansio.Event) -> None:
    """
    Apply resolution label automatically on the issues that were closed by a PR.
    """
    issue_data = event.data["issue"]
    for label_data in issue_data["labels"]:
        if label_data["name"].startswith("Resolution: "):
            return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)
    if await _has_closer(gh, issue_number=issue_data["number"]):
        await gh.post(issue_data["labels_url"], data=["Resolution: Fix Committed"])


async def _has_closer(gh: utils.GitHubAPI, *, issue_number: int) -> bool:
    data = await gh.graphql(
        GET_CLOSED_EVENT_QUERY, owner=UPSTREAM_USERNAME, name=REPO_NAME, issue_number=issue_number
    )
    return data["repository"]["issue"]["timelineItems"]["nodes"][0]["closer"] is not None


@gh_router.register("pull_request", action="edited")
async def check_merged_pr_body_for_new_closed_issues(event: sansio.Event) -> None:
    """
    Auto-close and apply resolution label on linked issues
    of the already merged PR on body edit.
    """
    if "body" not in event.data["changes"] or not event.data["pull_request"]["merged"]:
        return

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)
    await _update_issue_resolutions_from_pr(gh, pr_number=event.data["number"])


@utils.interval_job(minutes=5)
async def poll_new_connected_events() -> None:
    """
    Polls the Issue Events API for `connected` events as they do not trigger
    the `pull_request.edited` event.

    On `connected` event, it does the same action that is done
    on `pull_request.edited` event here.
    """
    gh = await utils.get_gh_client()
    issue_events_url = f"/repos/{UPSTREAM_REPO}/issues/events"
    events = await gh.getitem(issue_events_url)
    for event_data in events:
        if latest_event_id >= event_data["id"]:
            return
        if event_data["event"] != "connected":
            continue
        issue_data = event_data["issue"]
        if "pull_request" not in issue_data:
            continue

        await _update_issue_resolutions_from_pr(gh, pr_number=issue_data["number"])


async def _update_issue_resolutions_from_pr(gh: utils.GitHubAPI, *, pr_number: int) -> None:
    closing_issue_refs = await _get_closing_issue_refs(gh, pr_number=pr_number)
    for issue_data in closing_issue_refs:
        for label_data in issue_data["labels"]["nodes"]:
            if label_data["name"].startswith("Resolution: "):
                break
        else:
            issue_url = f"/repos/{UPSTREAM_REPO}/issues/{issue_data['number']}"
            labels_url = f"{issue_url}/labels"
            await gh.post(labels_url, data=["Resolution: Fix Committed"])
            if not issue_data["closed"]:
                await gh.patch(issue_url, data={"state": "closed"})


async def _get_closing_issue_refs(gh: utils.GitHubAPI, *, pr_number: int) -> list[Any]:
    data = await gh.graphql(
        GET_CLOSING_ISSUE_REFERENCES_QUERY,
        owner=UPSTREAM_USERNAME,
        name=REPO_NAME,
        pr_number=pr_number,
    )
    return data["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]


@gh_router.register("workflow", action="completed")
async def apply_resolution_merged_on_release(event: sansio.Event) -> None:
    workflow_data = event.data["workflow"]
    if workflow_data["path"] != ".github/workflows/publish_release.yml":
        return
    if workflow_data["conclusion"] != "success":
        return

    workflow_run_data = event.data["workflow_run"]
    tag_name = workflow_run_data["head_branch"]
    if tag_name is None:
        log.error("No tag name found for workflow run with ID: %s", workflow_run_data["id"])
        return

    async with utils.git_lock:
        await utils.check_call("git", "fetch", "upstream")
        previous_tag = await utils.check_output(
            "git", "describe", "--abbrev=0", "--tags", f"{tag_name}~"
        )
        previous_tag_date = await utils.check_output(
            "git", "tag", "-l", previous_tag, "--format", "%(creatordate:iso-strict)"
        )

    installation_id = event.data["installation"]["id"]
    gh = await utils.get_gh_client(installation_id)

    operations = await _fetch_issues_resolved_by_release(
        gh, tag_name=tag_name, previous_tag_date=previous_tag_date
    )
    await _update_resolution_labels(gh, tag_name=tag_name, operations=operations)


async def _fetch_issues_resolved_by_release(
    gh: utils.GitHubAPI, *, tag_name: str, previous_tag_date: str
) -> list[dict[str, str]]:
    after = None
    has_next_page = True
    issue_numbers_to_label: list[int] = []
    operations: list[dict[str, str]] = []
    while has_next_page:
        data = await gh.graphql(
            GET_PR_HISTORY_QUERY,
            owner=UPSTREAM_USERNAME,
            name=REPO_NAME,
            tag_name=tag_name,
            after=after,
            since=previous_tag_date,
        )
        history = data["repository"]["ref"]["target"]["history"]
        for commit_data in history["nodes"]:
            associated_prs = commit_data["associatedPullRequests"]["nodes"]
            if not associated_prs:
                continue
            associated_pr_data = associated_prs[0]
            closing_issue_refs = associated_pr_data["closingIssuesReferences"]["nodes"]
            for issue_data in closing_issue_refs:
                if not issue_data["closed"]:
                    log.info(
                        "Issue %s (related to PR %s) is not closed, skipping...",
                        issue_data["number"],
                        associated_pr_data["number"],
                    )
                    continue

                if _has_resolution_fix_committed(issue_data, associated_pr_data):
                    issue_numbers_to_label.append(issue_data["number"])
                    operations.append({"labelable_id": json.dumps(issue_data["id"])})

        page_info = history["pageInfo"]
        after = page_info["endCursor"]
        has_next_page = page_info["hasNextPage"]

    log.info(
        "Finished fetching issues resolved by release %s:\n%r", tag_name, issue_numbers_to_label
    )

    return operations


def _has_resolution_fix_committed(
    issue_data: dict[str, Any], associated_pr_data: dict[str, Any]
) -> bool:
    resolution = None
    for label_data in issue_data["labels"]["nodes"]:
        if label_data["name"].startswith("Resolution: "):
            resolution = label_data["name"]
            break
    else:
        if resolution is not None:
            log.info(
                "Issue %s (related to PR %s) has a different resolution already, skipping...",
                issue_data["number"],
                associated_pr_data["number"],
            )
        else:
            log.info(
                "Issue %s (related to PR %s) does not have any resolution, skipping...",
                issue_data["number"],
                associated_pr_data["number"],
            )
        return False

    if resolution == "Resolution: Fix Committed":
        return True
    else:
        log.info(
            "Issue %s (related to PR %s) is not closed, skipping...",
            issue_data["number"],
            associated_pr_data["number"],
        )

    return False


async def _update_resolution_labels(
    gh: utils.GitHubAPI, *, tag_name: str, operations: list[dict[str, Any]]
) -> None:
    labels_url = f"/repos/{UPSTREAM_REPO}/labels{{/name}}"
    labels_to_add = [
        (await gh.getitem(labels_url, {"name": "Resolution: Fix Released"}))["node_id"]
    ]
    labels_to_remove = [
        (await gh.getitem(labels_url, {"name": "Resolution: Fix Committed"}))["node_id"]
    ]

    builder = utils.GraphQLMultiOperationCallBuilder(
        operation_type=utils.GraphQLOperationType.MUTATION,
        template=ADD_AND_REMOVE_LABELS_MUTATION_TMPL,
        operations=operations,
        common_substitutions={
            "labels_to_add": json.dumps(labels_to_add),
            "labels_to_remove": json.dumps(labels_to_remove),
        },
    )

    for call in builder.iter_calls():
        await gh.graphql(call)

    log.info("Labels of all issues resolved by release %s have been updated.", tag_name)
