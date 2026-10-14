import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error, request

import pytest
from jira_agent import cli


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv('JIRA_URL', 'https://example.atlassian.net')
    monkeypatch.setenv('JIRA_EMAIL', 'test@example.invalid')
    monkeypatch.setenv('JIRA_API_TOKEN', 'secret-never-print')
    monkeypatch.delenv('JIRA_CLOUD_ID', raising=False)


@pytest.fixture
def fake(monkeypatch):
    calls = []
    answers = []
    def call(self, method, path, body=None, query=None, write=False):
        calls.append((method, path, body, query, write))
        return answers.pop(0)
    monkeypatch.setattr(cli.Client, 'call', call)
    return calls, answers


def invoke(argv):
    return cli.execute(cli.parser().parse_args(argv))


def fieldfile(tmp_path, obj):
    p = tmp_path / 'fields.json'
    p.write_text(json.dumps(obj))
    return str(p)


def test_configuration(monkeypatch):
    c = cli.Client()
    assert c.base == 'https://example.atlassian.net/rest/api/3'
    monkeypatch.setenv('JIRA_CLOUD_ID', 'cloud-id')
    assert cli.Client().base == 'https://api.atlassian.com/ex/jira/cloud-id/rest/api/3'
    monkeypatch.delenv('JIRA_CLOUD_ID')
    for url in ('http://example.atlassian.net', 'https://example.atlassian.net@evil.test', 'https://example.atlassian.net/evil', 'https://evil.test', 'https://example.atlassian.net:443', ''):
        monkeypatch.setenv('JIRA_URL', url)
        with pytest.raises(cli.JiraError): cli.Client()
    monkeypatch.setenv('JIRA_URL', 'https://example.atlassian.net')
    monkeypatch.setenv('JIRA_API_TOKEN', '')
    with pytest.raises(cli.JiraError): cli.Client()


@pytest.mark.parametrize('argv,key,path', [
    (['doctor'], 'accountId', '/myself'),
    (['issue', 'DEMO-1'], 'id', '/issue/DEMO-1'),
    (['transitions', 'DEMO-1'], 'transitions', '/issue/DEMO-1/transitions'),
    (['projects'], 'values', '/project/search'),
    (['issue-types', 'DEMO'], 'issueTypes', '/issue/createmeta/DEMO/issuetypes'),
    (['fields', 'DEMO', '10001'], 'fields', '/issue/createmeta/DEMO/issuetypes/10001'),
])
def test_read_routes(fake, argv, key, path):
    calls, answers = fake
    expected = {key: [] if key not in ('id','accountId') else '1', 'total': 2}
    if argv[0] == 'issue':
        expected.update(key='DEMO-1', fields={})
    answers.append(expected)
    assert invoke(argv) == expected
    assert calls[0][0:2] == ('GET', path)
    answers.append({})
    with pytest.raises(cli.JiraError): invoke(argv)


def test_users_search_pagination(fake):
    calls, answers = fake
    answers.extend([[{'accountId':'abc'}], {'issues':[], 'nextPageToken':'next'}, {'issues':[]}])
    assert invoke(['users','Test','--start-at','50','--limit','20'])[0]['accountId'] == 'abc'
    assert calls[-1][3] == {'startAt':50,'maxResults':20,'query':'Test'}
    assert invoke(['search','project = DEMO'])['nextPageToken'] == 'next'
    invoke(['search','project = DEMO','--next-page-token','next','--fields','summary','--limit','10'])
    assert calls[-1] == ('POST','/search/jql',{'jql':'project = DEMO','maxResults':10,'fields':['summary'],'nextPageToken':'next'},None,False)
    with pytest.raises(cli.JiraError): invoke(['search',' '])


@pytest.mark.parametrize('value', ['../bad','a/b','a?x','', 'name space'])
def test_unsafe_identifier(value):
    with pytest.raises(cli.JiraError): cli.identifier(value)


@pytest.mark.parametrize('argv', [['projects','--limit','0'],['users','x','--start-at','-1'],['search','x','--limit','101'],['assign','D-1']])
def test_argparse(argv):
    with pytest.raises(SystemExit) as e: cli.parser().parse_args(argv)
    assert e.value.code == 2


def test_write_preview_and_execute(tmp_path, fake, monkeypatch):
    calls, answers = fake
    fields = fieldfile(tmp_path, {'project':{'key':'DEMO'},'issuetype':{'id':'1'},'summary':'A task'})
    text = tmp_path / 'comment.txt'
    text.write_text('Hello\n\nSecond paragraph')
    cases = [
        (['create','--fields-file',fields], 'POST','/issue',{'id':'2','key':'DEMO-2'}),
        (['update','DEMO-1','--fields-file',fields],'PUT','/issue/DEMO-1',None),
        (['comment','DEMO-1','--text-file',str(text)],'POST','/issue/DEMO-1/comment',{'id':'3'}),
        (['assign','DEMO-1','--account-id','abc:123'],'PUT','/issue/DEMO-1/assignee',None),
        (['assign','DEMO-1','--unassign'],'PUT','/issue/DEMO-1/assignee',None),
        (['transition','DEMO-1','31'],'POST','/issue/DEMO-1/transitions',None),
    ]
    for argv, method,path,result in cases:
        before = len(calls)
        preview = invoke(argv)
        assert preview['sent'] is False and preview['method'] == method and preview['path'] == path
        assert len(calls) == before
        answers.append(result)
        assert invoke(argv+['--confirm'])['success']
        assert calls[-1][4] is True
    assert calls[4][2] == {'accountId':None}
    adf = cli.comment_body(str(text))['body']
    assert adf['content'][1]['content'] == []
    with pytest.raises(cli.JiraError): invoke(['assign','D-1','--account-id',' '])
    with pytest.raises(cli.JiraError): invoke(['create','--fields-file',fieldfile(tmp_path, {'summary':'only'})])
    answers.append({})
    with pytest.raises(cli.JiraError): invoke(['update','D-1','--fields-file',fields,'--confirm'])
    answers.append({'id':''})
    with pytest.raises(cli.JiraError): invoke(['comment','D-1','--text-file',str(text),'--confirm'])
    monkeypatch.delenv('JIRA_EMAIL')
    assert invoke(['assign','D-1','--unassign'])['preview']


@pytest.mark.parametrize('obj',[[],{}, {'fields':{}},{'update':{}},{'description':'plain'}, {'number':float('nan')}])
def test_bad_fields(tmp_path,obj):
    with pytest.raises(cli.JiraError): cli.fields_file(fieldfile(tmp_path,obj))


def test_files(tmp_path):
    for func in (cli.fields_file,cli.comment_body):
        with pytest.raises(cli.JiraError): func(str(tmp_path/'absent'))
    p = tmp_path/'bad'
    p.write_bytes(b'\xff')
    for func in (cli.fields_file,cli.comment_body):
        with pytest.raises(cli.JiraError): func(str(p))
    p.write_text(' ')
    with pytest.raises(cli.JiraError): cli.comment_body(str(p))
    p.write_text('x'*(cli.MAX_BYTES+1))
    for func in (cli.fields_file,cli.comment_body):
        with pytest.raises(cli.JiraError): func(str(p))
    adf={'description':{'type':'doc','version':1,'content':[]}}
    assert cli.fields_file(fieldfile(tmp_path,adf)) == adf
    assert cli.fields_file(fieldfile(tmp_path,{'description':None})) == {'description':None}


def test_cli_output(capsys, fake):
    fake[1].append({'accountId':'abc'})
    assert cli.run(['doctor']) == 0
    assert json.loads(capsys.readouterr().out)['accountId'] == 'abc'
    assert cli.run(['issue','../bad']) == 1
    assert 'error' in json.loads(capsys.readouterr().out)


@pytest.fixture
def server():
    state={'status':200,'body':b'{"ok":true}','requests':[],'location':'https://evil.invalid/'}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            size=int(self.headers.get('Content-Length',0))
            state['requests'].append((self.command,self.path,self.headers,self.rfile.read(size)))
            self.send_response(state['status'])
            self.send_header('Location',state['location'])
            self.end_headers()
            self.wfile.write(state['body'])
        do_POST=do_GET
        do_PUT=do_GET
        def log_message(self,*args): pass
    srv=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=srv.serve_forever,daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{srv.server_port}',state
    srv.shutdown()
    srv.server_close()
    thread.join()


def test_http_contract(server):
    url,state=server
    c=cli.Client()
    c.base=url # Test transport only; production config rejects HTTP/localhost.
    assert c.call('GET','/path',query={'q':'a b'}) == {'ok':True}
    assert state['requests'][-1][1] == '/path?q=a+b'
    assert state['requests'][-1][2]['Authorization'] == c.authorization
    c.call('POST','/search/jql',{'jql':'project = DEMO'})
    assert json.loads(state['requests'][-1][3]) == {'jql':'project = DEMO'}
    state['body']=b''
    state['status']=204
    assert c.call('POST','/issue/DEMO-1/transitions',{},write=True) is None
    for status in (301,302,307,308,400,401,403,404,429,500):
        state.update(status=status,body=b'secret-never-print')
        with pytest.raises(cli.JiraError) as e: c.call('POST','/write',{},write=True)
        assert str(status) in str(e.value) and 'uncertain' in str(e.value)
        assert 'secret-never-print' not in str(e.value)
    state.update(status=200,body=b'not json')
    with pytest.raises(cli.JiraError,match='invalid JSON'): c.call('GET','/read')
    state['body']=b'x'*(cli.MAX_BYTES+1)
    with pytest.raises(cli.JiraError,match='4 MiB'): c.call('GET','/read')


def test_network_error(monkeypatch):
    c=cli.Client()
    for exc in (error.URLError('secret-never-print'),OSError('secret-never-print'),TimeoutError()):
        def fail(*args,**kwargs): raise exc
        monkeypatch.setattr(c.opener,'open',fail)
        with pytest.raises(cli.JiraError) as e: c.call('GET','/read')
        assert 'secret-never-print' not in str(e.value)
        assert 'uncertain' not in str(e.value)


def test_process_cli():
    result=subprocess.run([sys.executable,'-m','jira_agent.cli','assign','DEMO-1','--unassign'],capture_output=True,text=True,env={**os.environ,'PYTHONPATH':str(Path(__file__).parents[1]/'src')})
    assert result.returncode == 0
    assert json.loads(result.stdout)['body'] == {'accountId':None}


def test_strict_response_and_fields(server,tmp_path,monkeypatch):
    from http.client import IncompleteRead
    url,state=server
    monkeypatch.setenv('JIRA_URL','https://example.atlassian.net')
    monkeypatch.setenv('JIRA_EMAIL','test@example.invalid')
    monkeypatch.setenv('JIRA_API_TOKEN','secret-never-print')
    monkeypatch.delenv('JIRA_CLOUD_ID',raising=False)
    c=cli.Client()
    c.base=url
    state.update(status=202,body=b'')
    with pytest.raises(cli.JiraError,match='unverified'): c.call('PUT','/issue/D-1',{},write=True)
    state.update(status=201,body=b'{"id":"1","key":"D-1"}')
    assert c.call('POST','/issue',{},write=True)['key']=='D-1'
    def truncated(*args,**kwargs): raise IncompleteRead(b'private',100)
    monkeypatch.setattr(c.opener,'open',truncated)
    with pytest.raises(cli.JiraError,match='uncertain'): c.call('POST','/issue',{},write=True)
    with pytest.raises(cli.JiraError): cli.nonempty({'accountId':None},'accountId')
    for obj in ({'summary':[]},{'summary':' '},{'project':42},{'issuetype':True},{'project':{'key':''}}):
        with pytest.raises(cli.JiraError): cli.fields_file(fieldfile(tmp_path,obj))


@pytest.mark.parametrize('issue,expected_code', [('DEMO-1', 0), ('../bad', 1)])
def test_module_entrypoint(monkeypatch, capsys, issue, expected_code):
    """Exercise the module's actual exit wrapper with an offline preview/error."""
    import runpy

    monkeypatch.setattr(sys, 'argv', ['jira-agent', 'assign', issue, '--unassign'])
    with pytest.raises(SystemExit) as result:
        runpy.run_path(cli.__file__, run_name='__main__')
    assert result.value.code == expected_code
    payload = json.loads(capsys.readouterr().out)
    if expected_code:
        assert 'Identifier must contain' in payload['error']
    else:
        assert payload == {'preview': True, 'method': 'PUT', 'path': '/issue/DEMO-1/assignee',
                           'body': {'accountId': None}, 'sent': False}
