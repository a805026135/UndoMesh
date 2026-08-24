from pathlib import Path

import pytest

from miniclaude.execution import sandbox
from miniclaude.execution.sandbox import (
    BubblewrapSandboxBackend,
    ProcessSandboxBackend,
    SandboxLevel,
    create_sandbox_backend,
    detect_sandbox,
    sanitized_subprocess_env,
)
from miniclaude.security.credentials import KeyringCredentialStore, MemoryCredentialStore


def test_shell_environment_drops_credentials() -> None:
    env = sanitized_subprocess_env({"PATH": "bin", "DASHSCOPE_API_KEY": "secret", "HOME": "private"})
    assert env == {"PATH": "bin"}


def test_sandbox_report_never_overclaims() -> None:
    report = detect_sandbox()
    assert report.level in {SandboxLevel.PROCESS, SandboxLevel.OS}
    if report.level == SandboxLevel.PROCESS:
        assert not report.network_isolated and not report.filesystem_isolated


def test_process_sandbox_preserves_command(tmp_path: Path) -> None:
    prepared = ProcessSandboxBackend().prepare(["python", "-V"], str(tmp_path))
    assert prepared.argv == ("python", "-V")
    assert prepared.cwd == str(tmp_path)
    assert prepared.report.level == SandboxLevel.PROCESS


def test_bubblewrap_builds_project_scoped_network_isolated_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    backend = BubblewrapSandboxBackend(project)
    prepared = backend.prepare(["bash", "-c", "pwd"], str(nested))
    assert prepared.argv[0] == "/usr/bin/bwrap"
    assert "--unshare-net" in prepared.argv
    assert "--unshare-pid" in prepared.argv
    assert ("--bind", str(project.resolve()), str(project.resolve())) == tuple(
        prepared.argv[prepared.argv.index("--bind"):prepared.argv.index("--bind") + 3]
    )
    assert prepared.report.network_isolated
    with pytest.raises(ValueError, match="outside"):
        backend.prepare(["pwd"], str(tmp_path))

    filtered = BubblewrapSandboxBackend(project, seccomp_fd=9).prepare(["true"], str(project))
    assert filtered.pass_fds == (9,)
    assert ("--seccomp", "9") == tuple(filtered.argv[filtered.argv.index("--seccomp"):filtered.argv.index("--seccomp") + 2])


def test_sandbox_backend_selection_is_explicit(tmp_path: Path) -> None:
    assert isinstance(create_sandbox_backend("process", tmp_path), ProcessSandboxBackend)
    with pytest.raises(ValueError, match="Unknown sandbox"):
        create_sandbox_backend("container", tmp_path)


def test_memory_and_keyring_stores() -> None:
    memory = MemoryCredentialStore()
    memory.set("qwen", "secret")
    assert memory.get("qwen") == "secret"
    memory.delete("qwen")
    assert memory.get("qwen") is None

    values = {}
    backend = type("Backend", (), {
        "get_password": lambda self, service, user: values.get((service, user)),
        "set_password": lambda self, service, user, value: values.__setitem__((service, user), value),
        "delete_password": lambda self, service, user: values.pop((service, user)),
    })()
    keyring = KeyringCredentialStore(backend)
    keyring.set("qwen", "stored")
    assert keyring.get("qwen") == "stored"
    keyring.delete("qwen")
