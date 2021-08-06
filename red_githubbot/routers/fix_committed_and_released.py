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
