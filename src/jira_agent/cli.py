"""Bounded Jira Cloud REST v3 client; credentials never appear in CLI arguments."""
import argparse
import base64
import json
from http.client import HTTPException
import os
from pathlib import Path
import re
import sys
from urllib import error, parse, request

MAX_BYTES = 4 * 1024 * 1024


class JiraError(Exception):
    pass


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def identifier(value):
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise JiraError('Identifier must contain only letters, numbers, underscores or hyphens.')
    return value


def bounded(value):
    number = int(value)
    if not 1 <= number <= 100:
        raise argparse.ArgumentTypeError('Must be between 1 and 100.')
    return number


def offset(value):
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError('Must be nonnegative.')
    return number


class Client:
    def __init__(self):
        url = os.environ.get('JIRA_URL', '').rstrip('/')
        cloud = os.environ.get('JIRA_CLOUD_ID')
        if cloud:
            url = 'https://api.atlassian.com/ex/jira/' + identifier(cloud)
        if not re.fullmatch(r'https://(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.atlassian\.net|api\.atlassian\.com/ex/jira/[A-Za-z0-9_-]+)', url):
            raise JiraError('Set JIRA_URL to https://your-site.atlassian.net, or set JIRA_CLOUD_ID for the Atlassian gateway.')
        email, token = os.environ.get('JIRA_EMAIL', ''), os.environ.get('JIRA_API_TOKEN', '')
        if not email.strip() or not token.strip():
            raise JiraError('Set JIRA_EMAIL and JIRA_API_TOKEN in your environment; do not put credentials in arguments or files in this repository.')
        self.base = url + '/rest/api/3'
        self.authorization = 'Basic ' + base64.b64encode(f'{email}:{token}'.encode()).decode()
        self.opener = request.build_opener(request.ProxyHandler({}), NoRedirect())

    def call(self, method, path, body=None, query=None, write=False):
        url = self.base + path
        if query:
            url += '?' + parse.urlencode(query)
        data = None if body is None else json.dumps(body, allow_nan=False).encode()
        req = request.Request(url, data=data, method=method, headers={
            'Authorization': self.authorization, 'Accept': 'application/json', 'Content-Type': 'application/json',
        })
        uncertainty = ' Write outcome may be uncertain; inspect the issue before retrying.' if write else ''
        try:
            with self.opener.open(req, timeout=30) as response:
                expected = 201 if write and method == 'POST' and (path == '/issue' or path.endswith('/comment')) else 204 if write else 200
                if response.status != expected:
                    raise JiraError('Unexpected HTTP success status; operation success is unverified.' + uncertainty)
                raw = response.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    raise JiraError('Response exceeds the 4 MiB limit.' + uncertainty)
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except (ValueError, UnicodeDecodeError):
                    raise JiraError('Jira returned invalid JSON.' + uncertainty) from None
        except error.HTTPError as exc:
            exc.close()
            hint = {401: 'Check credentials.', 403: 'Check permissions and token scopes.', 404: 'Check the issue, project or endpoint.', 429: 'Rate limited; wait before a manual retry.'}.get(exc.code, 'Request failed; inspect configuration and fields.')
            raise JiraError(f'Jira HTTP {exc.code}. {hint}' + uncertainty) from None
        except (error.URLError, OSError, HTTPException):
            raise JiraError('Could not complete the Jira request.' + uncertainty) from None


def require(data, kind, key=None):
    if not isinstance(data, kind) or (key is not None and key not in data):
        raise JiraError('Unexpected Jira response shape; operation success is unverified. Inspect Jira before retrying writes.')
    return data


def nonempty(data, key):
    require(data, dict, key)
    if not isinstance(data[key], str) or not data[key]:
        raise JiraError('Unexpected Jira identifier; success is unverified. Inspect Jira before retrying writes.')
    return data


def fields_file(path):
    try:
        if Path(path).stat().st_size > MAX_BYTES:
            raise JiraError('Input file exceeds 4 MiB.')
        fields = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise JiraError('Cannot read fields file as UTF-8 JSON.') from None
    if not isinstance(fields, dict) or not fields:
        raise JiraError('Fields file must be a nonempty JSON object of Jira field names and values.')
    if any(k in fields for k in ('fields', 'update')):
        raise JiraError('Supply the field map directly, without a fields or update wrapper.')
    if 'description' in fields and fields['description'] is not None:
        desc = fields['description']
        if not isinstance(desc, dict) or desc.get('type') != 'doc' or desc.get('version') != 1 or not isinstance(desc.get('content'), list):
            raise JiraError('Description must be an Atlassian Document Format (ADF) document or null.')
    for key in ('project', 'issuetype'):
        if key in fields:
            item = fields[key]
            if not isinstance(item, dict) or not any(isinstance(item.get(k), str) and item[k].strip() for k in ('id', 'key', 'name')):
                raise JiraError(f'{key} must be an object with a nonempty string identifier.')
    if 'summary' in fields and (not isinstance(fields['summary'], str) or not fields['summary'].strip()):
        raise JiraError('Summary must be a nonempty string.')
    try:
        json.dumps(fields, allow_nan=False)
    except ValueError:
        raise JiraError('Fields JSON must not contain NaN or infinity.') from None
    return fields


def comment_body(path):
    try:
        if Path(path).stat().st_size > MAX_BYTES:
            raise JiraError('Input file exceeds 4 MiB.')
        text = Path(path).read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise JiraError('Cannot read comment file as UTF-8 text.') from None
    if not text.strip():
        raise JiraError('Comment must not be empty.')
    return {'body': {'type': 'doc', 'version': 1, 'content': [
        {'type': 'paragraph', 'content': [{'type': 'text', 'text': line}] if line else []}
        for line in text.splitlines()
    ]}}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('doctor', help='Read authenticated Jira identity; does not verify billing tier.')
    for name in ('projects', 'users', 'issue-types', 'fields'):
        child = sub.add_parser(name)
        child.add_argument('--limit', type=bounded, default=50)
        child.add_argument('--start-at', type=offset, default=0)
        if name in ('issue-types', 'fields'):
            child.add_argument('project')
        if name == 'fields':
            child.add_argument('issue_type')
        if name == 'users':
            child.add_argument('query')
    search = sub.add_parser('search')
    search.add_argument('jql')
    search.add_argument('--limit', type=bounded, default=50)
    search.add_argument('--next-page-token')
    search.add_argument('--fields', default='summary,status,assignee,priority')
    for name in ('issue', 'transitions', 'update', 'comment', 'assign', 'transition', 'create'):
        child = sub.add_parser(name)
        if name != 'create':
            child.add_argument('issue')
        if name in ('create', 'update'):
            child.add_argument('--fields-file', required=True)
        if name == 'comment':
            child.add_argument('--text-file', required=True)
        if name == 'assign':
            group = child.add_mutually_exclusive_group(required=True)
            group.add_argument('--account-id')
            group.add_argument('--unassign', action='store_true')
        if name == 'transition':
            child.add_argument('transition_id')
        if name not in ('issue', 'transitions'):
            child.add_argument('--confirm', action='store_true', help='Perform the authorized write once; default is a local preview.')
    return p


def execute(args):
    command = args.command
    path = '/issue/' + identifier(args.issue) if hasattr(args, 'issue') else ''
    if command in ('create', 'update', 'comment', 'assign', 'transition'):
        method = 'POST'
        if command in ('create', 'update'):
            body = {'fields': fields_file(args.fields_file)}
            if command == 'create':
                if not all(body['fields'].get(k) for k in ('project', 'issuetype', 'summary')):
                    raise JiraError('Create requires project, issuetype and summary fields; use issue-types and fields to discover requirements.')
                path = '/issue'
            else:
                method = 'PUT'
        elif command == 'comment':
            path += '/comment'
            body = comment_body(args.text_file)
        elif command == 'assign':
            path += '/assignee'
            method = 'PUT'
            if args.account_id is not None and not args.account_id.strip():
                raise JiraError('Account ID must not be blank; use --unassign for no assignee.')
            body = {'accountId': None if args.unassign else args.account_id}
        else:
            path += '/transitions'
            body = {'transition': {'id': identifier(args.transition_id)}}
        if not args.confirm:
            return {'preview': True, 'method': method, 'path': path, 'body': body, 'sent': False}
        result = Client().call(method, path, body, write=True)
        if command in ('create', 'comment'):
            require(result, dict, 'id')
            if not isinstance(result['id'], str) or not result['id']:
                raise JiraError('Missing valid result ID; inspect Jira before retrying the write.')
        elif result is not None:
            raise JiraError('Unexpected response to write; inspect Jira before retrying.')
        if command == 'create':
            nonempty(result, 'key')
        return {'success': True, 'operation': command, 'result': result}
    client = Client()
    if command == 'search':
        if not args.jql.strip():
            raise JiraError('JQL must not be empty.')
        body = {'jql': args.jql, 'maxResults': args.limit, 'fields': args.fields.split(',')}
        if args.next_page_token:
            body['nextPageToken'] = args.next_page_token
        result = require(client.call('POST', '/search/jql', body), dict, 'issues')
        require(result['issues'], list)
        return result
    if command in ('projects', 'users', 'issue-types', 'fields'):
        query = {'maxResults': args.limit, 'startAt': args.start_at}
        if command == 'projects':
            path = '/project/search'
        elif command == 'users':
            path = '/user/search'
            query['query'] = args.query
        else:
            path = '/issue/createmeta/' + identifier(args.project) + '/issuetypes'
            if command == 'fields':
                path += '/' + identifier(args.issue_type)
        result = client.call('GET', path, query=query)
        if command == 'users':
            return require(result, list)
        key = {'projects': 'values', 'issue-types': 'issueTypes', 'fields': 'fields'}[command]
        require(result, dict, key)
        require(result[key], list)
        return result
    if command == 'doctor':
        return nonempty(client.call('GET', '/myself'), 'accountId')
    if command == 'transitions':
        result = require(client.call('GET', path + '/transitions'), dict, 'transitions')
        require(result['transitions'], list)
        return result
    result = nonempty(client.call('GET', path), 'id')
    nonempty(result, 'key')
    require(result.get('fields'), dict)
    return result


def run(argv=None):
    args = parser().parse_args(argv)
    try:
        print(json.dumps(execute(args), ensure_ascii=False))
        return 0
    except JiraError as exc:
        print(json.dumps({'error': str(exc)}))
        return 1


if __name__ == '__main__':  # pragma: no cover
    sys.exit(run())
