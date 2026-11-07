"""LOCAL_CRYPTO_CONTRACT only; official Auth evidence is auth_tests/g3auth."""
import json
import time
import unittest
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from engine.cloud_auth import AuthError, AuthVerifier, TrustedKey, VerifiedIdentity


class AuthContracts(unittest.TestCase):
    def setUp(self):
        self.secret = "local-contract-secret-with-over-32-bytes"
        self.user, self.session = uuid4(), uuid4()
        self.live = True
        self.calls = []
        self.claims = {"iss": "http://contract.invalid/auth/v1", "aud": "authenticated",
                       "iat": int(time.time()), "exp": int(time.time()) + 600,
                       "sub": str(self.user), "session_id": str(self.session),
                       "role": "authenticated", "is_anonymous": False}
        self.verifier = AuthVerifier(issuer=self.claims["iss"],
                                    keys={"local": TrustedKey("HS256", self.secret)},
                                    session_checker=self.check)

    def check(self, user, session):
        self.calls.append((user, session))
        return self.live

    def token(self, changes=None, **kwargs):
        return jwt.encode(self.claims | (changes or {}), self.secret,
                          algorithm="HS256", headers={"kid": "local"} | kwargs)

    def test_signed_token_and_uncached_revocation(self):
        token = self.token()
        self.assertEqual(self.verifier.verify(token), VerifiedIdentity(self.user, self.session))
        self.live = False
        with self.assertRaises(AuthError):
            self.verifier.verify(token)
        self.assertEqual(len(self.calls), 2)

    def test_issuer_audience_expiry_iat_required_claims_and_uuid(self):
        variants = [{"iss": "https://attacker.invalid"}, {"aud": "service_role"},
                    {"aud": ["authenticated"]}, {"exp": int(time.time()) - 5},
                    {"iat": int(time.time()) + 120}, {"sub": "not-uuid"},
                    {"session_id": None}, {"exp": "9999999999"}, {"iat": True}]
        for changes in variants:
            with self.subTest(changes=changes), self.assertRaises(AuthError):
                self.verifier.verify(self.token(changes))
        for missing in self.claims:
            claims = dict(self.claims)
            del claims[missing]
            with self.subTest(missing=missing), self.assertRaises(AuthError):
                self.verifier.verify(jwt.encode(claims, self.secret, algorithm="HS256", headers={"kid": "local"}))
        self.assertEqual(self.calls, [])

    def test_service_role_and_anonymous_signin_rejected(self):
        for changes in ({"role": "service_role"}, {"role": "anon"}, {"is_anonymous": True}):
            with self.subTest(changes=changes), self.assertRaises(AuthError):
                self.verifier.verify(self.token(changes))

    def test_metadata_never_grants_identity_or_organization(self):
        token = self.token({"user_metadata": {"role": "service_role", "organization_id": str(uuid4()),
                                               "sub": str(uuid4())}, "app_metadata": {"org": str(uuid4())}})
        self.assertEqual(self.verifier.verify(token), VerifiedIdentity(self.user, self.session))

    def test_algorithm_key_and_token_url_confusion_rejected_without_callback(self):
        for headers in ({"kid": "unknown"}, {"jku": "http://169.254.169.254/"},
                        {"x5u": "file:///etc/passwd"}, {"jwk": {}}, {"crit": ["exp"]}):
            with self.subTest(headers=headers), self.assertRaises(AuthError):
                self.verifier.verify(self.token(**headers))
        with self.assertRaises(AuthError):
            self.verifier.verify(jwt.encode(self.claims, "", algorithm="none", headers={"kid": "local"}))
        with self.assertRaises(AuthError):
            self.verifier.verify(jwt.encode(self.claims, self.secret + "wrong", algorithm="HS256", headers={"kid": "local"}))
        self.assertEqual(self.calls, [])

    def test_session_store_unavailable_fails_closed(self):
        def unavailable(*args):
            raise TimeoutError("database down")
        self.verifier.session_checker = unavailable
        with self.assertRaisesRegex(AuthError, "Invalid credential"):
            self.verifier.verify(self.token())

    def test_es256_and_rs256_public_jwks(self):
        for algorithm, key in (("ES256", ec.generate_private_key(ec.SECP256R1())),
                               ("RS256", rsa.generate_private_key(public_exponent=65537, key_size=2048))):
            jwk = json.loads(jwt.algorithms.get_default_algorithms()[algorithm].to_jwk(key.public_key()))
            jwk.update(alg=algorithm, kid="public")
            verifier = AuthVerifier(issuer=self.claims["iss"],
                                    keys={"public": TrustedKey.from_jwk(jwk)}, session_checker=self.check)
            token = jwt.encode(self.claims, key, algorithm=algorithm, headers={"kid": "public"})
            self.assertEqual(verifier.verify(token).user_id, self.user)
            with self.assertRaises(AuthError):
                verifier.verify(self.token(kid="public"))

    def test_public_jwk_rejects_secret_private_and_weak_keys(self):
        for value in ({"alg": "HS256", "kty": "oct", "k": "secret"},
                      {"alg": "ES256", "kty": "EC", "crv": "P-384"},
                      {"alg": "RS256", "kty": "RSA", "d": "private"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                TrustedKey.from_jwk(value)
        key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key())) | {"alg": "RS256"}
        with self.assertRaises(ValueError):
            TrustedKey.from_jwk(jwk)

    def test_size_bounds_and_malformed_tokens(self):
        for token in (None, "", "a" * 16385, "Bearer token", "x.y.z"):
            with self.subTest(size=len(token) if token else 0), self.assertRaises(AuthError):
                self.verifier.verify(token)


if __name__ == "__main__":
    unittest.main()
