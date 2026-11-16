"""Regression tests for the upstream UIS TOTP authentication continuation."""
import contextlib
import io
import unittest
from unittest.mock import Mock, patch

from src.api import webvpn

SEED = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 public test seed.


def response(data=None, text="", url="https://example.test/"):
    result = Mock(status_code=200, text=text, url=url, history=[])
    result.json.return_value = data
    return result


class TotpTests(unittest.TestCase):
    def setUp(self):
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)

    def test_standard_vectors(self):
        for stamp, expected in [(59, "287082"), (1111111109, "081804"),
                                (1111111111, "050471"), (1234567890, "005924"),
                                (2000000000, "279037"), (20000000000, "353130")]:
            with self.subTest(timestamp=stamp):
                self.assertEqual(webvpn.totp_code(SEED, stamp), expected)

    def challenge(self):
        return {"code": 200, "second": True, "moduleCodes": ["userAndOtp"],
                "requestNumber": "request", "authChainCode": "otp-chain", "userName": "private-name"}

    def test_cas_challenge_completes_before_ticket_exchange(self):
        vpn = webvpn.WebVPNSession()
        vpn._encrypt_password = Mock(return_value="encrypted")
        vpn.session = Mock()
        vpn.session.get.side_effect = [response(), response(url="https://example.test/?lck=context"),
                                      response({"data": "public-key"}), response(), response({"code": 0})]
        vpn.session.post.side_effect = [
            response({"data": [{"moduleCode": "userAndPwd", "authChainCode": "password-chain"}]}),
            response(self.challenge()),
            response({"code": 200, "second": False, "loginToken": "complete-token"}),
            response(text='locationValue = "https://icourse.fudan.edu.cn/?ticket=opaque"'),
        ]
        with patch.object(webvpn.config, "TOTP_SECRET", SEED):
            self.assertTrue(vpn.authenticate_icourse("student", "password"))
        otp = vpn.session.post.call_args_list[2]
        self.assertEqual(otp.kwargs["json"]["authModuleCode"], "userAndOtp")
        self.assertEqual(otp.kwargs["json"]["requestNumber"], "request")
        self.assertEqual(otp.kwargs["json"]["authChainCode"], "otp-chain")
        self.assertEqual(otp.kwargs["json"]["authPara"]["loginName"], "private-name")
        code = otp.kwargs["json"]["authPara"]["otpCode"]
        self.assertRegex(code, r"^\d{6}$")
        self.assertEqual(vpn.session.post.call_args_list[3].kwargs["data"], {"loginToken": "complete-token"})
        self.assertNotIn(SEED, self.output.getvalue())
        self.assertNotIn(code, self.output.getvalue())
        self.assertNotIn("private-name", self.output.getvalue())

    def test_webvpn_continues_using_direct_idp(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.post = Mock(side_effect=[response(self.challenge()),
            response({"code": 200, "second": False, "loginToken": "complete-token"})])
        with patch.object(webvpn.config, "TOTP_SECRET", SEED):
            token = vpn._auth_execute("student", "encrypted", "context", "entity", "chain", "type")
        self.assertEqual(token, "complete-token")
        self.assertEqual(vpn.session.post.call_args.args[0], webvpn.config.IDP_BASE + "/idp/authn/authExecute")

    def test_failed_second_factor_does_not_restart_password_login(self):
        import main
        vpn = Mock()
        vpn.authenticate_icourse.side_effect = webvpn.SecondFactorError("TOTP rejected")
        with (patch.object(main, "WebVPNSession", return_value=vpn) as factory,
              patch.object(main.time, "sleep") as sleep,
              self.assertRaises(webvpn.SecondFactorError)):
            main.login_with_retry()
        factory.assert_called_once()
        sleep.assert_not_called()

    def test_missing_or_invalid_seed_is_redacted(self):
        for seed in ("", "private-invalid-key", "123456"):
            with self.subTest(seed_present=bool(seed)), self.assertRaises(webvpn.SecondFactorError) as error:
                webvpn.totp_code(seed, 59)
            if seed:
                self.assertNotIn(seed, str(error.exception))

    def test_unsupported_module_or_missing_context_makes_no_otp_request(self):
        vpn = webvpn.WebVPNSession()
        vpn.session.post = Mock()
        for challenge in ({"moduleCodes": ["sms"]}, {"moduleCodes": ["userAndOtp"]}):
            with self.assertRaises(webvpn.SecondFactorError):
                vpn._authenticate_totp(challenge, "student", "context", "entity", "type")
        vpn.session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
