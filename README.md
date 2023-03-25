# Red GitHub Bot

[![Uptime](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FCog-Creators%2FRed-Status%2Fmaster%2Fapi%2Fred-git-hub-bot%2Fuptime.json)](https://status.discord.red/)
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

- **[DISABLED]** Auto-apply **Resolution: Fix Committed** and **Resolution: Fix Released** labels

    The bot auto-applies **Resolution: Fix Committed** label on the issues that were
    closed by a PR.

    On successful run of Publish Release workflow on the repository, the bot fetches associated PRs
    for all commits that were made between previous tag and the newly tagged release,
    and auto-applies **Resolution: Fix Released** label on the linked issues that were closed by
    that PR and had **Resolution: Fix Committed** label.

- Auto-apply **Changelog Entry: Pending** label on PRs merged to `V3/develop`

    The bot auto-applies **Changelong Entry: Pending** label on the PRs that were
    merged to `V3/develop` branch if there is no other **Changelon Entry** label on it already.

Doesn't seem like much? Don't worry, we're still working on more!

## Deployment

### Running on fly.io

1. [Create a GitHub App](https://github.com/settings/apps/new).
    - Set GitHub App name and Homepage URL (this can be repository URL).
    - Deselect "Active" checkbox under the Webhook section - we will set it up later.
    - Select needed "Repository permissions"
        - "Checks", "Contents", "Issues", "Pull Requests" are used by this application
    - Create the GitHub App
1. [Make a machine account](https://github.com/signup).
1. Deploy the application to fly.io.

    - Create the application on fly.io

        ```
        $ flyctl launch
        An existing fly.toml file was found for app red-githubbot
        ? Would you like to copy its configuration to the new app (y/N) Yes
        Creating app in /../../Red-GitHubBot
        Scanning source code
        Detected a Dockerfile app
        ? Choose an app name (leave blank to generate one): red-githubbot
        ? Select Organization: cog-creators (cog-creators)
        ? Choose a region for deployment: San Jose, California (US) (sjc)
        Created app 'red-githubbot' in organization 'cog-creators'
        Admin URL: https://fly.io/apps/red-githubbot
        Hostname: red-githubbot.fly.dev
        ? Would you like to set up a Postgresql database now? Yes
        ? Select configuration: Development - Single node, 1x shared CPU, 256MB RAM, 1GB disk
        Creating postgres cluster in organization cog-creators
        Creating app...
        Setting secrets on app red-githubbot-db...
        Provisioning 1 of 1 machines with image flyio/postgres-flex:15.2@sha256:8e00d751bb9811bc8511d7129db2cc5a515449cf4a7def8f1d60faacb2be91c6
        Waiting for machine to start...
        Machine ... is created
        ==> Monitoring health checks
          Waiting for ... to become healthy (started, 3/3)

        Postgres cluster red-githubbot-db created
          Username:    postgres
          Password:    ...
          Hostname:    red-githubbot-db.internal
          Flycast:     ...
          Proxy port:  5432
          Postgres port:  5433
          Connection string: postgres://postgres:...@red-githubbot-db.flycast:5432

        Save your credentials in a secure place -- you won't be able to see them again!

        Connect to postgres
        Any app within the cog-creators organization can connect to this Postgres using the above connection string

        Now that you've set up Postgres, here's what you need to understand: https://fly.io/docs/postgres/getting-started/what-you-should-know/
        Checking for existing attachments
        Registering attachment
        Creating database
        Creating user

        Postgres cluster red-githubbot-db is now attached to red-githubbot
        The following secret was added to red-githubbot:
          DATABASE_URL=postgres://red_githubbot:...@red-githubbot-db.flycast:5432/red_githubbot?sslmode=disable
        Postgres cluster red-githubbot-db is now attached to red-githubbot
        ? Would you like to set up an Upstash Redis database now? No
        ? Would you like to deploy now? No
        Your app is ready! Deploy with `flyctl deploy`
        ```

    - Set GitHub App ID under `GH_APP_ID` variable

        ```
        flyctl secrets set GH_APP_ID=...
        ```

    - Generate a webhook secret and set it under `GH_WEBHOOK_SECRET` variable

        ```
        flyctl secrets set GH_WEBHOOK_SECRET="$(python -c "print(__import__('secrets').token_hex(32))")"
        ```

    - Generate a private key in GitHub App's settings and copy the contents of downloaded key to `GH_PRIVATE_KEY` variable

        ```
        flyctl secrets set GH_PRIVATE_KEY=- < key.pem
        ```

    - Generate a personal access token for the bot's machine account and set it under `GH_AUTH` variable

        ```
        flyctl secrets set GH_AUTH=...
        ```

    - Deploy the application
1. Add a webhook to the GitHub App.
    - Use `https://{app_name}.herokuapp.com/webhook` as Webhook URL
    - Use the previously generated webhook secret (used in `GH_WEBHOOK_SECRET` variable) as Webhook secret
    - Save changes
1. Install the App on selected repositories.

### Running on Heroku

1. [Create a GitHub App](https://github.com/settings/apps/new).
    - Set GitHub App name and Homepage URL (this can be repository URL).
    - Deselect "Active" checkbox under the Webhook section - we will set it up later.
    - Select needed "Repository permissions"
        - "Checks", "Contents", "Issues", "Pull Requests" are used by this application
    - Create the GitHub App
1. [Make a machine account](https://github.com/signup).
1. Deploy the application to Heroku.

    [![Deploy](https://www.herokucdn.com/deploy/button.svg)](https://heroku.com/deploy?template=https://github.com/Cog-Creators/Red-GitHubBot)

    - Set GitHub App ID under `GH_APP_ID` variable
    - Generate a webhook secret and set it under `GH_WEBHOOK_SECRET` variable

        ```
        python -c "print(__import__('secrets').token_hex(32))"
        ```

    - Generate a private key in GitHub App's settings and copy the contents of downloaded key to `GH_PRIVATE_KEY` variable
    - Generate a personal access token for the bot's machine account and set it under `GH_AUTH` variable
    - Deploy the application
1. Add a webhook to the GitHub App.
    - Use `https://{app_name}.herokuapp.com/webhook` as Webhook URL
    - Use the previously generated webhook secret (used in `GH_WEBHOOK_SECRET` variable) as Webhook secret
    - Save changes
1. Install the App on selected repositories.

### Sentry integration

To enable Sentry integration, you need to:

1. (if on Heroku) Enable [Dyno Metadata feature from Heroku Labs](https://devcenter.heroku.com/articles/dyno-metadata)
1. Set Sentry client key under `SENTRY_DSN` variable for error tracking.

    You can find this on the 'Client Keys (DSN)' page in the Sentry project's settings.

1. Re-deploy the application

## License

See the [LICENSE file](LICENSE) for details.

Huge thanks to [Mariatta](https://github.com/Mariatta)
and her [miss-islington bot](https://github.com/python/miss-islington),
as well as [Brett Cannon](https://github.com/brettcannon)
and his [bedevere bot](https://github.com/python/bedevere)
which were both a huge help when implementing backport-related functionality.
