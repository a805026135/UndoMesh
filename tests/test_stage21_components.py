import base64
import hashlib
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from undomesh.distribution.updates import UpdateVerifier, redact_crash_report
from undomesh.evaluation.standard import wilson_interval
from undomesh.execution.code_mode import ControlledToolProgram
from undomesh.plugins.manifest import PluginManifest
from undomesh.server.rpc import JSONRPCDispatcher


def test_plugin_manifest_rejects_escape_and_accepts_composition():
    value = PluginManifest(name="review-tools", version="1.0.0", skills=["skills/review"])
    assert value.skills == ["skills/review"]
    with pytest.raises(ValueError, match="relative"):
        PluginManifest(name="review-tools", version="1", skills=["../escape"])


def test_controlled_code_mode_rejects_extra_executable_fields():
    steps = ControlledToolProgram.parse({"steps": [{"tool": "read_file", "arguments": {"path": "a"}}]})
    assert steps[0].tool == "read_file"
    with pytest.raises(ValueError, match="only"):
        ControlledToolProgram.parse({"steps": [{"tool": "read_file", "arguments": {}, "python": "open('x','w')"}]})


def test_signed_update_and_crash_redaction(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes_raw()
    artifact = b"installer"
    payload = {"version": "1.0.0", "sha256": hashlib.sha256(artifact).hexdigest()}
    wire = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    envelope = {"payload": payload, "signature": base64.b64encode(private.sign(wire)).decode()}
    assert UpdateVerifier(public).verify(envelope, artifact)["version"] == "1.0.0"
    staged = UpdateVerifier(public).stage(envelope, artifact, tmp_path / "update.bin")
    assert staged.read_bytes() == artifact
    assert redact_crash_report({"error": "boom", "prompt": "private"})["prompt"] == "[REDACTED]"


def test_wilson_interval_is_bounded():
    low, high = wilson_interval(7, 10)
    assert 0 < low < 0.7 < high < 1


@pytest.mark.asyncio
async def test_jsonrpc_dispatch_validation_without_service():
    dispatcher = JSONRPCDispatcher(object())
    missing = await dispatcher.dispatch({"jsonrpc": "2.0", "id": 1, "method": "missing"})
    assert missing["error"]["code"] == -32601
