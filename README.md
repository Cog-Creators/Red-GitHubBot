# Red GitHub Bot

[![Discord Server](https://discordapp.com/api/guilds/133049272517001216/widget.png?style=shield)](https://discord.gg/red)
[![Support Red on Patreon](https://img.shields.io/badge/Support-Red!-red.svg)](https://www.patreon.com/Red_Devs)
[![Code Style: Black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Imports: isort](https://user-images.githubusercontent.com/6032823/111363465-600fe880-8690-11eb-8377-ec1d4d5ff981.png)](https://github.com/PyCQA/isort)
[![We use pre-commit!](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)
[![PRs welcome!](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

GitHub bot which helps out on [Red-DiscordBot's repository](https://github.com/Cog-Creators/Red-DiscordBot).

## What does this bot do?

- Automated backports

    Labeling a PR with **Needs Backport To 3.x** label will cause the bot to attempt to
    automatically backport the PR to the appropriate maintenance branch once it's merged.

- Copy labels from the original PR to the backport

    The bot copies the relevant labels (type, and some release indicators)
    from the original PR to the backport PR.

- Comment on the original PR about the backport

    To help find the backport PR from the original PR, the bot mentions it in a comment.
    This also acts as a notification about the automated backport being successfully made.

- Verify titles of PRs targetting the maintenance branch

    The bot adds a check run to the PR to indicate whether the PR contains a `[3.x]` prefix,
    and also leaves a small note in there if the title does not contain the original PR number.

- Check for Blocked labels

    The bot reports the Blocked status for all PRs based on the PR's labels
    to avoid accidental merges of PRs that should not yet be merged.

Doesn't seem like much? Don't worry, we're still working on more!

## Deployment

### Running on Heroku

1. [Create a GitHub App](https://github.com/settings/apps/new).
    - Set GitHub App name and Homepage URL (this can be repository URL).
    - Deselect "Active" checkbox under the Webhook section - we will set it up later.
    - Select needed "Repository permissions"
        - "Checks", "Contents", "Issues", "Pull Requests" are used by this application
    - Create the GitHub App
1. [Make a machine account](https://github.com/signup).
1. Deploy the application to Heroku.

    [![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/jack1142/Red-GitHubBot)

    - Set GitHub App ID under `GH_APP_ID` variable
    - Generate a webhook secret and set it under `GH_WEBHOOK_SECRET` variable

        ```
        python -c "print(__import__('secrets').token_hex(32))"
        ```

    - Generate a private key in GitHub App's settings and copy the contents of downloaded key to `GH_PRIVATE_KEY` variable
    - Generate a personal access token for the bot's machine account and set it under `GH_AUTH` variable
    - (optional) Set Sentry client key under `SENTRY_DSN` variable for error tracking.
    - Deploy the application
1. Add a webhook to the GitHub App.
    - Use `https://{app_name}.herokuapp.com/webhook` as Webhook URL
    - Use the previously generated webhook secret (used in `GH_WEBHOOK_SECRET` variable) as Webhook secret
    - Save changes
1. Install the App on selected repositories.

## License

See the [LICENSE file](LICENSE) for details.

Huge thanks to [Mariatta](https://github.com/Mariatta)
and her [miss-islington bot](https://github.com/python/miss-islington),
as well as [Brett Cannon](https://github.com/brettcannon)
and his [bedevere bot](https://github.com/python/bedevere)
which were both a huge help when implementing backport-related functionality.
