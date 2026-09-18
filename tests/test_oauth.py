import asyncio
import base64
import hashlib
import re
import secrets
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from daemon import db as db_module
from daemon.middleware.auth import AuthMiddleware
from daemon.routers import auth, mcp, oauth
from daemon.services import auth_service, oauth_service

CALLBACK = "https://claude.ai/api/mcp/auth_callback"
EMAIL, PASSWORD = "owner@example.com", "correct horse battery"


def _pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class OAuthFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = mock.patch.object(db_module, "DB_PATH", str(Path(self.tmp.name) / "hub.db"))
        self.db_patch.start()
        asyncio.run(db_module.init_db())
        # The first account is the admin, so the key tests below cover the key
        # that could do the most.
        self.api_key = asyncio.run(auth_service.create_user(EMAIL, PASSWORD))["api_key"]
        auth_service.invalidate_caches()
        oauth_service.invalidate_caches()
        oauth._pending.clear()

        app = FastAPI()
        app.add_middleware(AuthMiddleware)
        app.include_router(auth.router)
        app.include_router(mcp.router)
        app.include_router(oauth.router)

        # Stand-ins for the two places an API key is still good for.
        @app.get("/v1/models")
        @app.get("/api/system/connect")
        async def whoami(request: Request):
            return {"user": request.state.user["email"]}

        self.client = TestClient(app)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    # -- helpers ------------------------------------------------------------

    def _register(self, uris=(CALLBACK,)):
        r = self.client.post("/oauth/register", json={
            "redirect_uris": list(uris), "client_name": "Claude", "token_endpoint_auth_method": "none"})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["client_id"]

    def _authorize_params(self, client_id, challenge, **extra):
        return {"response_type": "code", "client_id": client_id, "redirect_uri": CALLBACK,
                "code_challenge": challenge, "code_challenge_method": "S256",
                "state": "xyz", "resource": "http://testserver/mcp", "scope": "mcp", **extra}

    def _code(self, client_id, challenge):
        r = self.client.get("/oauth/authorize", params=self._authorize_params(client_id, challenge))
        self.assertEqual(r.status_code, 200)
        rid = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
        r = self.client.post("/oauth/authorize", data={"request_id": rid, "action": "login",
                                                        "email": EMAIL, "password": PASSWORD})
        self.assertIn("Allow Claude?", r.text)
        r = self.client.post("/oauth/authorize", data={"request_id": rid, "action": "allow"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        location = urllib.parse.urlsplit(r.headers["location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", CALLBACK)
        query = dict(urllib.parse.parse_qsl(location.query))
        self.assertEqual((query["state"], query["iss"]), ("xyz", "http://testserver"))
        return query["code"]

    def _exchange(self, client_id, code, verifier, **extra):
        return self.client.post("/oauth/token", data={
            "grant_type": "authorization_code", "code": code, "client_id": client_id,
            "redirect_uri": CALLBACK, "code_verifier": verifier,
            "resource": "http://testserver/mcp", **extra})

    def _tools_list(self, token):
        return self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
                                headers={"Authorization": f"Bearer {token}"})

    # -- discovery ----------------------------------------------------------

    def test_unauthenticated_mcp_points_at_resource_metadata(self):
        r = self.client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r.status_code, 401)
        self.assertIn('resource_metadata="http://testserver/.well-known/oauth-protected-resource/mcp"',
                      r.headers["www-authenticate"])

    def test_metadata_documents(self):
        prm = self.client.get("/.well-known/oauth-protected-resource/mcp").json()
        self.assertEqual(prm["resource"], "http://testserver/mcp")
        self.assertEqual(prm["authorization_servers"], ["http://testserver"])
        asm = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertEqual(asm["issuer"], "http://testserver")
        self.assertEqual(asm["code_challenge_methods_supported"], ["S256"])
        self.assertTrue(asm["client_id_metadata_document_supported"])
        self.assertIn("none", asm["token_endpoint_auth_methods_supported"])

    def test_unknown_well_known_is_404_not_html(self):
        r = self.client.get("/.well-known/openid-configuration")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.headers["content-type"], "application/json")

    # -- the whole flow -----------------------------------------------------

    def test_full_flow_and_refresh_rotation(self):
        client_id = self._register()
        verifier, challenge = _pkce()
        tokens = self._exchange(client_id, self._code(client_id, challenge), verifier).json()
        self.assertEqual(tokens["token_type"], "Bearer")
        self.assertEqual(self._tools_list(tokens["access_token"]).status_code, 200)

        refreshed = self.client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_id})
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        self.assertNotEqual(refreshed.json()["refresh_token"], tokens["refresh_token"])
        reused = self.client.post("/oauth/token", data={
            "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
            "client_id": client_id})
        self.assertEqual(reused.json()["error"], "invalid_grant")

    def test_code_is_single_use_and_pkce_is_enforced(self):
        client_id = self._register()
        verifier, challenge = _pkce()
        code = self._code(client_id, challenge)
        wrong = self._exchange(client_id, code, secrets.token_urlsafe(48))
        self.assertEqual(wrong.json()["error"], "invalid_grant")
        # the failed attempt burned the code
        self.assertEqual(self._exchange(client_id, code, verifier).json()["error"], "invalid_grant")

    def test_oauth_token_does_not_open_other_apis(self):
        client_id = self._register()
        verifier, challenge = _pkce()
        access = self._exchange(client_id, self._code(client_id, challenge), verifier).json()["access_token"]
        fresh = TestClient(self.client.app)  # no session cookie
        r = fresh.get("/api/auth/me", headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(r.status_code, 401)

    def test_bad_bearer_on_mcp_says_invalid_token(self):
        r = self._tools_list("sah-oat-not-a-real-token")
        self.assertEqual(r.status_code, 401)
        self.assertIn('error="invalid_token"', r.headers["www-authenticate"])

    # -- what the API key opens ---------------------------------------------

    def _with_key(self, method, path, **kw):
        fresh = TestClient(self.client.app)  # no session cookie
        return fresh.request(method, path, headers={"Authorization": f"Bearer {self.api_key}"}, **kw)

    def test_api_key_runs_models_and_finds_the_hub(self):
        for path in ("/v1/models", "/api/system/connect"):
            r = self._with_key("GET", path)
            self.assertEqual(r.status_code, 200, path)
            self.assertEqual(r.json()["user"], EMAIL)

    def test_api_key_does_not_open_the_hub_api(self):
        self.assertEqual(self._with_key("GET", "/api/auth/me").status_code, 401)
        self.assertEqual(self._with_key("POST", "/api/auth/me/api-key/rotate").status_code, 401)
        # x-api-key is the same key by another header
        fresh = TestClient(self.client.app)
        self.assertEqual(fresh.get("/api/auth/me", headers={"x-api-key": self.api_key}).status_code, 401)

    def test_api_key_opens_mcp(self):
        r = self._with_key("POST", "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertEqual(r.status_code, 200, r.text)

    def test_api_key_does_not_read_media(self):
        self.assertEqual(self._with_key("GET", "/images/anything.png").status_code, 401)

    # -- refusals -----------------------------------------------------------

    def test_unregistered_redirect_uri_is_not_followed(self):
        client_id = self._register()
        _, challenge = _pkce()
        r = self.client.get("/oauth/authorize", params=self._authorize_params(
            client_id, challenge, redirect_uri="https://evil.example/cb"), follow_redirects=False)
        self.assertEqual(r.status_code, 400)
        self.assertNotIn("location", r.headers)

    def test_missing_pkce_redirects_with_error(self):
        client_id = self._register()
        params = self._authorize_params(client_id, "x")
        params.pop("code_challenge")
        r = self.client.get("/oauth/authorize", params=params, follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertIn("error=invalid_request", r.headers["location"])

    def test_consent_without_the_viewing_session_is_refused(self):
        client_id = self._register()
        _, challenge = _pkce()
        r = self.client.get("/oauth/authorize", params=self._authorize_params(client_id, challenge))
        rid = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
        # A forged cross-site POST arrives with no session cookie.
        r = TestClient(self.client.app).post("/oauth/authorize", data={"request_id": rid, "action": "allow"},
                                             follow_redirects=False)
        self.assertEqual(r.status_code, 403)

    def test_wrong_password_stays_on_sign_in(self):
        client_id = self._register()
        _, challenge = _pkce()
        r = self.client.get("/oauth/authorize", params=self._authorize_params(client_id, challenge))
        rid = re.search(r'name="request_id" value="([^"]+)"', r.text).group(1)
        r = self.client.post("/oauth/authorize", data={"request_id": rid, "action": "login",
                                                        "email": EMAIL, "password": "nope-nope"})
        self.assertEqual(r.status_code, 401)
        self.assertIn("Incorrect email or password", r.text)

    def test_registration_rejects_non_https_redirects(self):
        r = self.client.post("/oauth/register", json={"redirect_uris": ["http://evil.example/cb"]})
        self.assertEqual(r.json()["error"], "invalid_redirect_uri")

    def test_client_id_metadata_document(self):
        cimd = "https://claude.ai/oauth/mcp-client-metadata"
        doc = {"client_id": cimd, "client_name": "Claude", "redirect_uris": [CALLBACK]}
        with mock.patch.object(oauth_service, "_fetch_metadata_document",
                               mock.AsyncMock(return_value=doc)):
            verifier, challenge = _pkce()
            tokens = self._exchange(cimd, self._code(cimd, challenge), verifier)
        self.assertEqual(tokens.status_code, 200, tokens.text)

    def test_loopback_redirects_ignore_port(self):
        self.assertTrue(oauth_service.redirect_matches(
            ["http://127.0.0.1/callback"], "http://127.0.0.1:53211/callback"))
        self.assertFalse(oauth_service.redirect_matches(
            ["http://127.0.0.1/callback"], "http://127.0.0.1:53211/other"))
        self.assertFalse(oauth_service.redirect_matches(
            ["https://claude.ai/api/mcp/auth_callback"], "https://claude.ai:444/api/mcp/auth_callback"))


if __name__ == "__main__":
    unittest.main()
