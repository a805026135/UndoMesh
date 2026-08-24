"""Offline record/replay/fork and provenance-policy demonstration."""

from pathlib import Path
import sys
from tempfile import TemporaryDirectory

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from undomesh.provenance.models import Provenance, SourceType, TrustLevel
from undomesh.provenance.policy import ProvenancePolicy
from undomesh.runtime.events import EventType
from undomesh.runtime.replay import ReplayEngine
from undomesh.runtime.store import SQLiteEventStore


def main() -> None:
    with TemporaryDirectory() as directory:
        store = SQLiteEventStore(Path(directory) / "events.db")
        session = "demo"
        store.append(session_id=session, event_type=EventType.TURN_STARTED, payload={"input": "Inspect safely"})
        store.append(session_id=session, event_type=EventType.TURN_ENDED, payload={"outcome": "completed"})
        replay = ReplayEngine(store)
        fork = replay.fork(session, new_session_id="demo-fork")
        result = replay.replay(fork)
        external = Provenance(SourceType.WEB_CONTENT, TrustLevel.EXTERNAL, "web:malicious")
        decision = ProvenancePolicy().evaluate("read_file", {"path": ".env"}, (external,))
        print({"fork": fork, "events": result.event_count, "side_effects": result.side_effects_executed, "security_decision": decision.action.value})
        store.close()


if __name__ == "__main__":
    main()
