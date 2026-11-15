import contextlib
import io
import unittest
from unittest.mock import Mock, patch

import requests

from src.api import webvpn


def response(data=None, status=200, text='', url='https://example.test/'):
    result = Mock(status_code=status, text=text, url=url, history=[])
    result.json.return_value = data
    return result


def icourse_session(auth_result, verification=None):
    vpn = webvpn.WebVPNSession()
    vpn.session = Mock(cookies=requests.cookies.RequestsCookieJar())
    vpn._encrypt_password = Mock(return_value='encrypted-password')
    vpn.session.get.side_effect = [
        response(),
        response(url='https://example.test/?lck=context'),
        response({'data': 'public-key'}),
        response(),
        response(verification if verification is not None else {'code': 0}),
    ]
    vpn.session.post.side_effect = [
        response({'data': [{'moduleCode': 'userAndPwd', 'authChainCode': 'chain'}]}),
        response(auth_result),
        response(text='locationValue = "https://icourse.fudan.edu.cn/?ticket=opaque"'),
    ]
    return vpn


class WebVPNAuthTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_real_cas_second_factor_response_is_explained(self):
        vpn = icourse_session({
            'code': 200, 'second': True, 'loginToken': None,
            'moduleCodes': ['second-factor'],
            'userName': 'private-name', 'mobile': 'private-phone',
        })
        with self.assertRaisesRegex(RuntimeError, 'requires additional authentication') as error:
            vpn.authenticate_icourse('student', 'password')
        self.assertNotIn('private-', str(error.exception))
        self.assertNotIn('private-', self.output.getvalue())
        self.assertEqual(vpn.session.post.call_count, 2)

    def test_webvpn_second_factor_is_explained(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.post = Mock(return_value=response({
            'code': 200, 'second': True, 'loginToken': None,
        }))
        with self.assertRaisesRegex(RuntimeError, 'requires additional authentication'):
            vpn._auth_execute('student', 'encrypted', 'ctx', 'entity', 'chain', 'type')

    def test_retry_loop_stops_on_actual_cas_challenge(self):
        vpn = icourse_session({'code': 200, 'second': True, 'loginToken': None})
        vpn.login = Mock(return_value=True)
        with patch.object(webvpn, 'WebVPNSession', return_value=vpn) as factory, \
             patch.object(webvpn.time, 'sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError, 'requires additional authentication'):
                webvpn.login_with_retry()
        factory.assert_called_once()
        sleep.assert_not_called()

    def test_transient_failure_still_uses_fresh_session(self):
        first, second = Mock(), Mock()
        first.login.side_effect = requests.exceptions.Timeout()
        with patch.object(webvpn, 'WebVPNSession', side_effect=[first, second]), \
             patch.object(webvpn.time, 'sleep'):
            self.assertIs(webvpn.login_with_retry(max_attempts=2), second)
        first.authenticate_icourse.assert_not_called()
        second.authenticate_icourse.assert_called_once()

    def test_http_200_without_vpn_cookie_is_not_login_success(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.get = Mock(return_value=response())
        with self.assertRaisesRegex(RuntimeError, 'WebVPN session'):
            vpn._establish_session('https://example.test/?ticket=opaque')
        self.assertNotIn('Session established', self.output.getvalue())

    def test_consumed_ticket_is_not_replayed_after_timeout(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.get = Mock(side_effect=[requests.exceptions.Timeout(), response()])
        with self.assertRaisesRegex(RuntimeError, 'fresh login'):
            vpn._establish_session('https://example.test/?ticket=opaque')
        self.assertEqual(vpn.session.get.call_count, 1)

    def test_timeout_with_cookie_requires_live_portal(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.cookies.set('wengine_vpn_ticketwebvpn_fudan_edu_cn', 'opaque')
        vpn.session.get = Mock(side_effect=[requests.exceptions.Timeout(), response(status=302)])
        with self.assertRaisesRegex(RuntimeError, 'WebVPN session'):
            vpn._establish_session('https://example.test/?ticket=opaque')
        self.assertEqual(vpn.session.get.call_count, 2)

    def test_timeout_with_live_session_is_accepted_without_ticket_replay(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.cookies.set('wengine_vpn_ticketwebvpn_fudan_edu_cn', 'opaque')
        vpn.session.get = Mock(side_effect=[requests.exceptions.Timeout(), response()])
        vpn._establish_session('https://example.test/?ticket=opaque')
        self.assertEqual(vpn.session.get.call_count, 2)
        self.assertEqual(vpn.session.get.call_args.args[0], webvpn.config.WEBVPN_BASE + '/')

    def test_cookie_and_live_portal_verify_session(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.cookies.set('wengine_vpn_ticketwebvpn_fudan_edu_cn', 'opaque')
        vpn.session.get = Mock(side_effect=[response(), response()])
        vpn._establish_session('https://example.test/?ticket=opaque')
        self.assertEqual(vpn.session.get.call_count, 2)

    def test_icourse_verification_failure_is_not_success(self):
        vpn = icourse_session({'code': 200, 'loginToken': 'opaque'}, {'code': 401})
        with self.assertRaisesRegex(RuntimeError, 'could not be verified'):
            vpn.authenticate_icourse('student', 'password')

    def test_icourse_verified_session_remains_successful(self):
        vpn = icourse_session({'code': 200, 'second': False, 'loginToken': 'opaque'})
        self.assertTrue(vpn.authenticate_icourse('student', 'password'))


if __name__ == '__main__':
    unittest.main()
