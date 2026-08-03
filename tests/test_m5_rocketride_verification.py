"""Focused tests for M5 evidence fingerprints, audits, and sanitization."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.verify_rocketride_cloud import (
    direct_anthropic_imports,
    reasoning_received,
    token_fingerprint,
    validate_sanitized_evidence,
)


def minimal_evidence() -> dict[str, object]:
    return {
        "token_fingerprint": token_fingerprint("fixture-only-token"),
        "project_uuid": "5621bc5a-2ff1-4021-8592-81383fa4890f",
        "passes": {"pipeline_continuity": True},
    }


class M5RocketRideVerificationTests(unittest.TestCase):
    def test_token_is_reduced_to_known_sha256_fingerprint(self) -> None:
        self.assertEqual(
            token_fingerprint("fixture-only-token"),
            "c09eb36fcc6af7593256999af93603a1ea47c204fdbbc1631bf97bc7257af66f",
        )

    def test_sanitized_evidence_accepts_only_the_fingerprint_token_field(
        self,
    ) -> None:
        evidence = minimal_evidence()
        validate_sanitized_evidence(evidence)

        evidence["pipeline_token"] = "plaintext-is-forbidden"
        with self.assertRaisesRegex(ValueError, "Forbidden token field"):
            validate_sanitized_evidence(evidence)

    def test_sanitized_evidence_rejects_hosts_and_credentials(self) -> None:
        for key, value in (
            ("cloud_host", "example.invalid"),
            ("credential", "redacted"),
            ("service_url", "https://example.invalid"),
        ):
            with self.subTest(key=key):
                evidence = minimal_evidence()
                evidence[key] = value
                with self.assertRaisesRegex(ValueError, "Forbidden sensitive field"):
                    validate_sanitized_evidence(evidence)

    def test_sanitized_evidence_rejects_endpoint_shaped_values(self) -> None:
        evidence = minimal_evidence()
        evidence["note"] = "https://example.invalid/private"
        with self.assertRaisesRegex(ValueError, "Forbidden endpoint"):
            validate_sanitized_evidence(evidence)

    def test_cloud_reasoning_must_not_be_local_fallback_text(self) -> None:
        self.assertTrue(reasoning_received("Graph evidence supports this action."))
        self.assertFalse(reasoning_received(""))
        self.assertFalse(reasoning_received("RocketRide returned no justification."))

    def test_direct_import_audit_excludes_virtual_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text(
                "from " + "anthropic import AsyncAnthropic\n",
                encoding="utf-8",
            )
            virtual_environment = root / ".venv"
            virtual_environment.mkdir()
            (virtual_environment / "ignored.py").write_text(
                "import " + "anthropic\n",
                encoding="utf-8",
            )

            self.assertEqual(direct_anthropic_imports(root), ["app.py:1"])


if __name__ == "__main__":
    unittest.main()
