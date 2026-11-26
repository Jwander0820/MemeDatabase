import json
import os
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import patch

import test_release_bundle as fixtures
from src.anime_memes.vps_publication import VpsPublication, config_value, write_json
from src.anime_memes.web import create_app


class VpsPublicationTest(unittest.TestCase):
    setUp = fixtures.ReleaseBundleTest.setUp

    def manager(self, *, fail_upload=False, block=None, installed=False, restricted=False):
        self.enterContext(patch.dict(os.environ, {'MEMES_VPS_SSH_DIR': '', 'MEMES_VPS_IMAGE': 'memedatabase-public-api',
                                                'MEMES_VPS_RESTRICTED_SSH': '1' if restricted else '0'}))
        self.enterContext(patch('src.anime_memes.vps_publication.shutil.which', return_value='ssh'))
        self.commands = []

        def run(args, **kwargs):
            self.assertFalse(kwargs['shell'])
            self.commands.append(args)
            output, code = '', 0
            if 'current.json' in args[-1] or args[-1].startswith('check-version '):
                output = json.dumps({'installed': installed, 'manifest': self.manifest if installed else None})
            elif ' activate ' in args[-1]:
                output = json.dumps(self.manifest)
            elif 'media-inventory' in args[-1]:
                if block:
                    block[0].set()
                    if not block[1].wait(5):
                        raise TimeoutError('test worker never released')
                output = json.dumps({'inventory_version': 1, 'media': self.manifest['media']})
            elif args[0] == 'scp':
                code = 1 if fail_upload else 0
            elif 'install-bundle' in args[-1]:
                output = json.dumps({'version': self.version, 'count': 1,
                                     'activated': '--activate' in args[-1]})
            elif args[-1] in {'install', 'install-and-activate'}:
                self.assertEqual(kwargs['stdin'].read(2), b'\x1f\x8b')
                output = json.dumps({'version': self.version, 'activated': args[-1] == 'install-and-activate'})
            return subprocess.CompletedProcess(args, code, output, 'PRIVATE-ERROR')

        manager = VpsPublication(self.releases, self.base/'state', run=run)
        manager.save_config({'ssh_host': 'deploy@example.test', 'release_root': '/srv/memedatabase/releases'})
        return manager

    def finish(self, manager):
        manager.thread.join(5)
        self.assertFalse(manager.thread.is_alive())
        return manager.snapshot()['job']

    def test_background_publish_and_persisted_result(self):
        manager = self.manager()
        started = manager.start({'version': self.version, 'activate': True})
        self.assertEqual(started['status'], 'running')
        job = self.finish(manager)
        self.assertEqual(job['status'], 'succeeded')
        self.assertEqual(job['bundle']['media'], 0)
        self.assertEqual(job['bundle']['media_reused'], 1)
        self.assertTrue(job['result']['activated'])
        restored = VpsPublication(self.releases, self.base/'state').snapshot()['job']
        self.assertEqual(restored['id'], job['id'])
        self.assertEqual(restored['status'], 'succeeded')
        self.assertTrue(any('rm -f' in args[-1] for args in self.commands))
        self.assertTrue(all('StrictHostKeyChecking=yes' in args for args in self.commands))

    def test_install_only_does_not_activate(self):
        manager = self.manager()
        manager.start({'version': self.version, 'activate': False})
        job = self.finish(manager)
        self.assertEqual(job['status'], 'succeeded')
        self.assertFalse(job['result']['activated'])

    def test_restricted_publish_streams_bundle_through_fixed_ssh_actions(self):
        manager = self.manager(restricted=True)
        manager.start({'version': self.version, 'activate': True})
        self.assertEqual(self.finish(manager)['status'], 'succeeded')
        self.assertEqual([args[-1] for args in self.commands],
                         [f'check-version {self.version}', 'media-inventory', 'install-and-activate'])
        self.assertTrue(all(args[0] == 'ssh' and '-T' in args for args in self.commands))

    def test_installed_version_can_be_activated_without_uploading_again(self):
        manager = self.manager(installed=True)
        manager.start({'version': self.version, 'activate': True})
        job = self.finish(manager)
        self.assertEqual(job['status'], 'succeeded')
        self.assertTrue(job['result']['already_installed'])
        self.assertTrue(any(' activate ' in args[-1] for args in self.commands))
        self.assertFalse(any(args[0] == 'scp' for args in self.commands))

    def test_concurrent_start_and_target_change_blocked(self):
        reached, release = threading.Event(), threading.Event()
        manager = self.manager(block=(reached, release))
        manager.start({'version': self.version, 'activate': False})
        try:
            self.assertTrue(reached.wait(3))
            with self.assertRaises(ValueError):
                manager.start({'version': self.version, 'activate': False})
            with self.assertRaises(ValueError):
                manager.save_config({'ssh_host': 'other.test', 'release_root': '/srv/releases'})
        finally:
            release.set()
            self.finish(manager)

    def test_upload_failure_stops_install_and_hides_remote_error(self):
        manager = self.manager(fail_upload=True)
        manager.start({'version': self.version, 'activate': True})
        job = self.finish(manager)
        self.assertEqual(job['status'], 'failed')
        self.assertFalse(any('install-bundle' in args[-1] for args in self.commands))
        self.assertNotIn('PRIVATE-ERROR', json.dumps(job))

    def test_restart_marks_uncertain_job_without_retry(self):
        manager = self.manager()
        write_json(manager.job_path, {'status': 'running', 'id': 'prior'})
        restored = VpsPublication(self.releases, self.base/'state')
        self.assertEqual(restored.snapshot()['job']['status'], 'interrupted')
        self.assertEqual(self.commands, [])

    def test_unexpected_remote_response_terminates_job(self):
        manager = self.manager()
        original_run = manager.run

        def malformed(args, **kwargs):
            if 'install-bundle' in args[-1]:
                return subprocess.CompletedProcess(args, 0, '[]', '')
            return original_run(args, **kwargs)

        manager.run = malformed
        manager.start({'version': self.version, 'activate': True})
        self.assertEqual(self.finish(manager)['status'], 'failed')

    def test_untrusted_arguments_rejected(self):
        for host, root in [('x;whoami', '/srv/releases'), ('-oProxyCommand=x', '/srv/releases'),
                           ('host', '/srv/../etc'), ('host', '/')]:
            with self.subTest(host=host, root=root), self.assertRaises(ValueError):
                config_value({'ssh_host': host, 'release_root': root})
        manager = self.manager()
        for data in ({'version': '../x', 'activate': True}, {'version': self.version, 'activate': 'yes'},
                     {'version': self.version, 'activate': True, 'command': 'whoami'}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                manager.start(data)
        self.assertEqual(self.commands, [])

    def test_private_api_uses_background_worker_and_validates_origin(self):
        manager = self.manager()
        app = create_app(db_path=self.base/'admin.sqlite3', release_root=self.releases)
        app.extensions['vps_publication'] = manager
        client = app.test_client()
        response = client.get('/api/publication/vps')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.json['versions'][0]['version'], self.version)
        self.assertEqual(client.post('/api/publication/vps', json={'version': self.version, 'activate': True},
                                     headers={'Origin': 'https://evil.example'}).status_code, 403)
        response = client.post('/api/publication/vps', json={'version': self.version, 'activate': False})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.finish(manager)['status'], 'succeeded')
