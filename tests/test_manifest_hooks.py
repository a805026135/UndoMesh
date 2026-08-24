import json

import pytest

from miniclaude.runtime.declarative_hooks import DeclarativeHookLoader
from miniclaude.runtime.hooks import HookBus, HookContext, HookPoint, HookVerdict
from miniclaude.security.manifest import LocalKeyRing, ManifestAuthenticator


def test_manifest_authentication_rotation_and_old_key_readability(tmp_path):
    keys = LocalKeyRing(tmp_path / "keys")
    auth = ManifestAuthenticator(keys)
    first = auth.seal({"files": ["a.py"]})
    old_key = first["key_id"]
    keys.rotate()
    assert auth.verify(first).payload == {"files": ["a.py"]}
    second = auth.seal({"files": ["b.py"]})
    assert second["key_id"] != old_key
    assert keys.audit({old_key, second["key_id"]})["missing"] == []
    first["payload"]["files"] = ["evil.py"]
    with pytest.raises(ValueError, match="authentication"):
        auth.verify(first)


@pytest.mark.asyncio
async def test_declarative_hook_requires_digest_trust_and_cannot_execute(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"schema_version": 1, "hooks": [{
        "name": "protect", "point": "pre_tool", "matcher": "apply_*",
        "action": "deny", "reason": "protected"
    }]}), encoding="utf-8")
    bus = HookBus()
    loader = DeclarativeHookLoader(set())
    with pytest.raises(PermissionError, match="approve digest"):
        loader.load(path, bus)
    loader = DeclarativeHookLoader({loader.digest(path)})
    assert loader.load(path, bus) == ["protect"]
    outcome = await bus.run(HookContext(HookPoint.PRE_TOOL, "s", tool_name="apply_patch_set"))
    assert outcome.verdict == HookVerdict.DENY


def test_declarative_hook_rejects_command_field(tmp_path):
    path = tmp_path / "hooks.json"
    path.write_text(json.dumps({"schema_version": 1, "hooks": [{
        "name": "bad", "point": "pre_tool", "action": "deny", "command": "calc.exe"
    }]}), encoding="utf-8")
    loader = DeclarativeHookLoader({DeclarativeHookLoader.digest(path)})
    with pytest.raises(ValueError, match="executable"):
        loader.load(path, HookBus())
