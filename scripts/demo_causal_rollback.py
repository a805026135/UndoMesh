"""Key-free multi-agent causal rollback demo with an optional desktop review panel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from miniclaude.agents.causal import CausalTaskLedger, CausalWorktreeCoordinator
from miniclaude.agents.contracts import TaskContract


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def build_demo(base: Path) -> dict[str, object]:
    repo = base / "project"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "demo@example.invalid")
    git(repo, "config", "user.name", "MiniClaude Demo")
    (repo / "schema.txt").write_text("schema-v1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")

    ledger = CausalTaskLedger(base / "causal.db")
    coordinator = CausalWorktreeCoordinator(repo, base / "worktrees", ledger)
    contracts = (
        TaskContract(
            task_id="schema-agent",
            agent_type="refactor_expert",
            objective="Upgrade schema contract",
            readable_paths=("schema.txt",),
            writable_paths=("schema.txt",),
        ),
        TaskContract(
            task_id="client-agent",
            agent_type="test_generator",
            parent_task_id="schema-agent",
            objective="Adapt client to schema v2",
            dependencies=("schema-agent",),
            readable_paths=("schema.txt",),
            writable_paths=("client.txt",),
        ),
    )
    changes = {"schema-agent": ("schema.txt", "schema-v2\n"), "client-agent": ("client.txt", "uses-schema-v2\n")}
    reviews = []
    for contract in contracts:
        worktree = coordinator.begin_task(contract)
        relative, content = changes[contract.task_id]
        (worktree.path / relative).write_text(content, encoding="utf-8")
        review = coordinator.review_task(
            contract,
            worktree,
            verification_argv=["git", "diff", "--check"],
            commit_message=f"agent: {contract.task_id}",
        )
        coordinator.approve_task(
            contract.task_id,
            review,
            approved_review_sha256=review.review_sha256,
        )
        reviews.append(review)

    plan = ledger.plan_rollback("schema-agent", strategy="cascade")
    candidate = coordinator.prepare_rollback(
        plan, verification_argv=["git", "diff", "--check"]
    )
    rollback_commit = coordinator.approve_rollback(
        candidate,
        approved_plan_sha256=plan.plan_sha256,
        approved_review_sha256=candidate.review.review_sha256,
    )
    result = {
        "root_task": plan.root_task_id,
        "strategy": plan.strategy,
        "affected_closure": list(plan.affected_task_ids),
        "rollback_order": list(plan.rollback_task_ids),
        "plan_sha256": plan.plan_sha256,
        "approved_review_sha256": candidate.review.review_sha256,
        "approved_tree": candidate.review.candidate_tree,
        "rollback_commit": rollback_commit,
        "workspace": {
            "schema": (repo / "schema.txt").read_text(encoding="utf-8").strip(),
            "client_exists": (repo / "client.txt").exists(),
        },
        "task_states": {record.task_id: record.state.value for record in ledger.list()},
    }
    ledger.close()
    return result


def show_ui(report: dict[str, object], *, screenshot: Path | None = None) -> None:
    import tkinter as tk
    from tkinter import ttk

    root = tk.Tk()
    root.title("MiniClaude · Causal Rollback Review")
    root.geometry("1120x700")
    root.configure(bg="#0d1117")
    root.minsize(900, 600)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Treeview", background="#111820", fieldbackground="#111820", foreground="#d8dee9", rowheight=34, borderwidth=0)
    style.configure("Treeview.Heading", background="#171f2a", foreground="#8b98a9", relief="flat", font=("Segoe UI", 10, "bold"))
    style.map("Treeview", background=[("selected", "#19324d")])

    shell = tk.Frame(root, bg="#0d1117", padx=32, pady=26)
    shell.pack(fill="both", expand=True)
    tk.Label(shell, text="CAUSAL ROLLBACK", bg="#0d1117", fg="#58a6ff", font=("Segoe UI", 10, "bold")).pack(anchor="w")
    tk.Label(shell, text="Multi-Agent recovery review", bg="#0d1117", fg="#f0f6fc", font=("Segoe UI Semibold", 25)).pack(anchor="w", pady=(6, 4))
    tk.Label(shell, text="Impact closure is computed before any write. Approval is bound to the plan, parent HEAD, candidate commit and tree.", bg="#0d1117", fg="#8b98a9", font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 22))

    cards = tk.Frame(shell, bg="#0d1117")
    cards.pack(fill="x")
    for title, value, color in (
        ("ROOT TASK", str(report["root_task"]), "#f0f6fc"),
        ("AFFECTED", str(len(report["affected_closure"])), "#f2cc60"),
        ("STRATEGY", str(report["strategy"]).upper(), "#58a6ff"),
        ("RESULT", "VERIFIED", "#3fb950"),
    ):
        card = tk.Frame(cards, bg="#111820", padx=18, pady=14, highlightbackground="#263241", highlightthickness=1)
        card.pack(side="left", fill="x", expand=True, padx=(0, 12))
        tk.Label(card, text=title, bg="#111820", fg="#788596", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        tk.Label(card, text=value, bg="#111820", fg=color, font=("Segoe UI Semibold", 15)).pack(anchor="w", pady=(5, 0))

    body = tk.Frame(shell, bg="#0d1117")
    body.pack(fill="both", expand=True, pady=(22, 0))
    tree = ttk.Treeview(body, columns=("dependency", "state", "action"), show="tree headings", height=6)
    tree.heading("#0", text="TASK")
    tree.heading("dependency", text="CAUSAL DEPENDENCY")
    tree.heading("state", text="STATE")
    tree.heading("action", text="ROLLBACK ORDER")
    tree.column("#0", width=230)
    tree.column("dependency", width=260)
    tree.column("state", width=180)
    tree.column("action", width=180)
    order = {name: index + 1 for index, name in enumerate(report["rollback_order"])}
    for task in report["affected_closure"]:
        dependency = "—" if task == report["root_task"] else str(report["root_task"])
        tree.insert("", "end", text=f"  {task}", values=(dependency, "ROLLED BACK", f"#{order[task]}"))
    tree.pack(fill="x")

    evidence = tk.Frame(body, bg="#111820", padx=20, pady=18, highlightbackground="#263241", highlightthickness=1)
    evidence.pack(fill="x", pady=(18, 0))
    tk.Label(evidence, text="APPROVAL EVIDENCE", bg="#111820", fg="#8b98a9", font=("Segoe UI", 9, "bold")).grid(row=0, column=0, sticky="w", columnspan=2)
    rows = (
        ("Plan SHA-256", str(report["plan_sha256"])),
        ("Review SHA-256", str(report["approved_review_sha256"])),
        ("Approved tree", str(report["approved_tree"])),
        ("Workspace", "schema-v1 restored · dependent client removed"),
    )
    for index, (label, value) in enumerate(rows, 1):
        tk.Label(evidence, text=label, bg="#111820", fg="#788596", font=("Segoe UI", 10), width=18, anchor="w").grid(row=index, column=0, sticky="w", pady=5)
        tk.Label(evidence, text=value, bg="#111820", fg="#d8dee9", font=("Cascadia Mono", 9), anchor="w").grid(row=index, column=1, sticky="w", pady=5)
    if screenshot is not None:
        from PIL import ImageGrab

        def capture() -> None:
            root.update_idletasks()
            x, y = root.winfo_rootx(), root.winfo_rooty()
            image = ImageGrab.grab(
                bbox=(x, y, x + root.winfo_width(), y + root.winfo_height()),
                all_screens=True,
            )
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            image.save(screenshot)
            root.destroy()

        root.after(700, capture)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ui", action="store_true", help="show the desktop review panel")
    parser.add_argument("--screenshot", type=Path, help="capture the populated review panel and exit")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="miniclaude-causal-demo-") as temporary:
        report = build_demo(Path(temporary))
        if args.ui or args.screenshot:
            show_ui(report, screenshot=args.screenshot)
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
