import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError

from intern_personal.core import Invalid
from intern_personal.github_api import GitHubAPI,GitHubError,NoRedirect,host_token


class GitHubAPITests(unittest.TestCase):
    def test_environment_token_never_uses_process_arguments(self):
        with patch.dict('os.environ',{'GH_TOKEN':'fixture-secret-only'},clear=True),patch('subprocess.run') as run:
            self.assertEqual(host_token(),'fixture-secret-only');run.assert_not_called()

    def test_builtin_store_only_and_literal_path_without_shell(self):
        results=[SimpleNamespace(returncode=0,stdout="!execute-arbitrary-command\nstore --file '/data/file;$(no-command)'\n"),
                 SimpleNamespace(returncode=0,stdout='username=fixture-owner\npassword=fixture-secret-only\n')]
        with patch.dict('os.environ',{},clear=True),patch('subprocess.run',side_effect=results) as run:
            self.assertEqual(host_token(),'fixture-secret-only')
            args=run.call_args_list[1].args[0]
            self.assertEqual(args,['/usr/bin/git','credential-store','--file','/data/file;$(no-command)','get'])
            self.assertNotIn('fixture-secret-only',repr(run.call_args_list));self.assertFalse(run.call_args_list[1].kwargs.get('shell',False))

    def test_unknown_helpers_require_secure_host_auth_without_execution(self):
        with patch.dict('os.environ',{},clear=True),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='!shell-code\ncustom-program\n')) as run:
            with self.assertRaises(Invalid):host_token()
            self.assertEqual(run.call_count,1)

    def client(self):
        return GitHubAPI(SimpleNamespace(config=lambda:{'codex_proxy':True}),token='fixture-secret-only')

    def test_create_private_empty_repository_has_fixed_host_and_version(self):
        api=self.client();response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.geturl.return_value='https://api.github.com/user/repos';response.read.return_value=b'{"id":1}'
        api.opener.open=Mock(return_value=response)
        api.create('example-owner','example-project','operation marker')
        request=api.opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.github.com/user/repos')
        body=json.loads(request.data);self.assertTrue(body['private']);self.assertFalse(body['auto_init'])
        self.assertEqual(request.get_header('X-github-api-version'),'2022-11-28')
        self.assertNotIn('fixture-secret-only',request.data.decode());api.close();self.assertIsNone(api.token)

    def test_error_response_never_echoes_credentials_and_only_get_404_is_missing(self):
        api=self.client();api.opener.open=Mock(side_effect=HTTPError('https://api.github.com/user/repos',404,'fixture-secret-only',{},io.BytesIO(b'fixture-secret-only')))
        self.assertIsNone(api.repository('example-owner','example-project'))
        with self.assertRaises(GitHubError) as captured:api.create('example-owner','example-project','operation')
        self.assertNotIn('fixture-secret-only',str(captured.exception));self.assertEqual(captured.exception.status,404)
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))

    def test_no_non_github_paths_or_redirect_following(self):
        api=self.client();api.opener.open=Mock()
        for owner,repo in [('example-owner','../elsewhere'),('https://elsewhere','project')]:
            with self.assertRaises(Invalid):api.repository(owner,repo)
        with self.assertRaises(Invalid):api.request('GET','/user?token=bad')
        api.opener.open.assert_not_called()


if __name__=='__main__':unittest.main()
