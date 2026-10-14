# Jira MCP

Source: [jira-mcp](https://github.com/java-heapler/jira-mcp). The executable and Python package names remain unchanged.

A standalone JSON CLI and agent skill for Jira Cloud REST API v3. Search issues, discover project fields, and preview authorized changes before sending them. It uses standard Jira capabilities available on Free, requires no paid add-on, and never creates accounts, starts trials, changes billing, or checks your subscription tier.

## Install and authenticate

```sh
uv tool install .
jira-agent --help
```

Choose Jira's actual **Free** plan when setting up your site, rather than a paid trial. Existing Jira permissions and API token scopes still apply. Configure `JIRA_EMAIL` and `JIRA_API_TOKEN` in your process environment through your secret manager; never paste tokens into chat, commands, committed files, or issues. For an unscoped token, set `JIRA_URL=https://your-site.atlassian.net`. For a scoped token, set `JIRA_CLOUD_ID` to your site's cloud ID; it takes precedence and routes to `https://api.atlassian.com/ex/jira/{cloudId}`. The CLI never discovers or logs credentials. `jira-agent doctor` reads the authenticated identity; it does not verify plan or billing status.

Official references: [Free plan](https://www.atlassian.com/software/jira/pricing), [API tokens and scoped gateway](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/), [REST v3](https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/).

## Read

```sh
jira-agent doctor
jira-agent projects --limit 50 --start-at 0
jira-agent search 'project = DEMO ORDER BY updated DESC' --limit 25
jira-agent issue DEMO-1
jira-agent issue-types DEMO
jira-agent fields DEMO 10001
jira-agent transitions DEMO-1
jira-agent users 'Example Name'
```

Commands return one bounded page and preserve server pagination metadata. For search, pass the returned `nextPageToken` with `--next-page-token`; stop when no token is returned or `isLast` is true. Search uses the enhanced `POST /search/jql` endpoint; this POST does not modify issues. For projects, issue-types, fields and users use `--start-at`; advance by the returned item count, stopping at `isLast`, total, or an empty page. Users returns an array without total. Limits range from 1 to 100. Server permissions can hide issues and users; an empty result does not prove no matching items exist. Searches may not immediately reflect recent writes.

## Writes

All writes default to a **local preview** requiring no credentials or network. Review that JSON, then add `--confirm` only for user-authorized work. No automatic retries, deletes, bulk writes, generic HTTP passthrough, billing, or add-on installation are exposed. Creation, assignment, comments, and transitions may notify other people.

```sh
jira-agent create --fields-file /path/to/fields.json
jira-agent update DEMO-1 --fields-file /path/to/changes.json
jira-agent comment DEMO-1 --text-file /path/to/comment.txt
jira-agent assign DEMO-1 --account-id ACCOUNT_ID
jira-agent assign DEMO-1 --unassign
jira-agent transition DEMO-1 31
```

The fields file is a map, not a `fields` wrapper. Example:

```json
{"project":{"key":"DEMO"},"issuetype":{"id":"10001"},"summary":"Implement collision handling","description":{"type":"doc","version":1,"content":[{"type":"paragraph","content":[{"type":"text","text":"Add tests for colliding keys."}]}]}}
```

Discover required/custom fields and allowed issue types first; project configuration varies. Rich `description` values use Atlassian Document Format (ADF), not Markdown strings. Comments convert a UTF-8 text file into ADF paragraphs. Retrieve transition IDs and account IDs with the read commands rather than guessing names. After an uncertain write error, inspect Jira before deciding to retry; the server might already have applied it.

## Boundaries and verification

Credentials go only to validated Atlassian HTTPS hosts, HTTP redirects and environment proxies are disabled, timeout is 30 seconds, and responses and input files are limited to 4 MiB. Error bodies and raw network exceptions are not printed, because they may contain secrets. Private issue data appears in successful JSON and previews; handle your terminal output accordingly. This tool has no persistent cache.

JSON stdout; exit 0 on success, 1 on application errors, 2 on invalid CLI syntax. The suite enforces 100% measured statement and branch coverage, including the module entry point. Coverage measures execution, not correctness across every Jira configuration. Synthetic tests cover transport, request payloads, pagination, write previews, error handling, and the command entry point. Real tenant configuration and custom-field workflows require a read-only live check after credentials are configured; synthetic tests do not prove live access.

```sh
uv run --extra dev pytest --cov=jira_agent --cov-branch --cov-report=term-missing --cov-fail-under=100
uv build
```

The skill is `skills/jira/SKILL.md`; install it using the same symlink convention as the other standalone application skills. This repository does not alter any central agent repository.
