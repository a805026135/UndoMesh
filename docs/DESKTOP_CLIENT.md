# Desktop Client

MiniClaude ships both a Rich terminal REPL and a zero-extra-dependency Tkinter desktop client. Installable entry points are:

```text
miniclaude
miniclaude-desktop
python -m miniclaude --gui
```

The Windows `run.pyw` launcher remains available for double-click startup.

## Interaction model

The client uses a restrained neutral workspace layout rather than a decorative chat window:

- a persistent task sidebar with recent event-store sessions;
- a focused conversation canvas and compact composer;
- visible model/provider control;
- task switching through append-only event projections;
- `Ctrl+N` for a new task and `Ctrl+R` for Recovery Center;
- tool execution progress and stop control;
- token/iteration status;
- recovery summary showing checkpoint and restore-receipt counts.

Recovery Center presents prompt checkpoints newest-first, their code health, source transaction count, conflicts and unavailable dependencies. Code-changing actions explain the preflight guarantee and require confirmation. Recent compensating restore receipts stay visible beside the selected checkpoint.

The design takes product-level inspiration from the public [OpenAI Developers](https://developers.openai.com/) and [Projects and chats](https://learn.chatgpt.com/docs/projects) documentation: persistent project/task navigation, visible progress, review and user supervision. It uses MiniClaude's own Tkinter components and recovery model and does not copy proprietary assets or UI implementation.

## Threading model

Tk runs on the main thread. MiniClaude's asyncio loop and SQLite event store are created on one background thread. UI updates use `root.after`; model/tool work never mutates Tk widgets directly. Approval callbacks marshal a modal confirmation to the UI thread and block the background operation until the user decides.

Checkpoint and restore operations remain on the event-store thread because Python SQLite connections are thread-affine. Long restores can occupy that background loop but do not freeze the desktop UI. A future client runtime should use a worker-safe application service and progress events for chunk-level restore status.
