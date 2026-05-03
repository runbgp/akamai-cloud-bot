import asyncio
import json
import os
import datetime
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any

# Keys we manage on top of the raw Linode instance dict.
NUDGE_KEY = "_nudge"
META_KEY = "_meta"


def _utcnow_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _default_nudge() -> Dict[str, Any]:
    return {
        "last_confirmed_at": _utcnow_iso(),
        "nudge_sent_at": None,
        "reminder_sent_at": None,
        "nudge_count": 0,
        "exempt": False,
        "last_dm_failed": False,
    }


class Database:
    """JSON-backed store for per-user Akamai Cloud instances and nudge state.

    The on-disk layout is one of:

      Legacy: {user_id: [instance, ...]}
      Current: {"_meta": {...}, "users": {user_id: [instance, ...]}}

    Legacy files are migrated on first read.

    Mutating methods are async and serialize through `self._lock` so that
    concurrent tasks (the auto-refresh loop, the nudge loop, button clicks,
    slash commands) can't clobber each other's read-modify-write cycles.
    Read methods stay sync — they take a single snapshot, which is atomic
    on a single-threaded asyncio loop.
    """

    def __init__(self, db_file: str = "akamai_instances.json"):
        self.db_file = db_file
        self._ensure_db_exists()
        # Constructed eagerly; asyncio.Lock binds to the running loop on first
        # acquire, so it's safe to instantiate here at import time.
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        """Hold the DB lock across multiple operations.

        Use this when a logical operation reads, decides, then writes — and
        another writer racing in between would produce an inconsistent
        result. Methods called *inside* a `transaction()` block must use the
        `_locked` private variants (or call back through the public methods
        only if the lock is reentrant — it isn't, so prefer `_locked`).
        """
        async with self._lock:
            yield

    def _ensure_db_exists(self):
        # Treat both "missing" and "exists but empty" as needing initialization —
        # `docker run` with a bind mount to a non-existent host file creates an
        # empty file, which would otherwise crash json.load on first read.
        if not os.path.exists(self.db_file) or os.path.getsize(self.db_file) == 0:
            with open(self.db_file, "w") as f:
                json.dump({"_meta": {}, "users": {}}, f)

    def _read_db(self) -> Dict[str, Any]:
        with open(self.db_file, "r") as f:
            data = json.load(f)

        if "users" not in data:
            # Legacy layout: top-level keys are user IDs.
            data = {"_meta": {}, "users": data}
        data.setdefault("_meta", {})
        data.setdefault("users", {})
        return data

    def _write_db(self, data: Dict[str, Any]):
        with open(self.db_file, "w") as f:
            json.dump(data, f, indent=2)

    # ----- meta (bot-wide) -----

    def get_meta(self) -> Dict[str, Any]:
        return self._read_db().get("_meta", {})

    async def set_meta(self, **kwargs):
        async with self._lock:
            data = self._read_db()
            data["_meta"].update(kwargs)
            self._write_db(data)

    # ----- instances -----

    async def add_instance(self, user_id: str, instance_data: Dict[str, Any]):
        """Upsert an instance under user_id (no cross-user uniqueness check).

        Used by the create flow where we know the VM was just created and
        can't already be tracked anywhere. For user-driven imports use
        `import_instance` instead.
        """
        async with self._lock:
            self._add_instance_locked(user_id, instance_data)

    def _add_instance_locked(self, user_id: str, instance_data: Dict[str, Any]):
        data = self._read_db()
        users = data["users"]
        users.setdefault(user_id, [])

        for i, instance in enumerate(users[user_id]):
            if instance.get("id") == instance_data.get("id"):
                merged = dict(instance_data)
                merged[NUDGE_KEY] = instance.get(NUDGE_KEY, _default_nudge())
                users[user_id][i] = merged
                self._write_db(data)
                return

        new_entry = dict(instance_data)
        new_entry[NUDGE_KEY] = _default_nudge()
        users[user_id].append(new_entry)
        self._write_db(data)

    async def import_instance(
        self, user_id: str, instance_data: Dict[str, Any]
    ) -> bool:
        """Atomically add an instance only if no user already tracks its id.

        Returns True if added, False if it was already tracked under any user.
        """
        async with self._lock:
            data = self._read_db()
            target_id = instance_data.get("id")
            for instances in data["users"].values():
                for inst in instances:
                    if inst.get("id") == target_id:
                        return False

            users = data["users"]
            users.setdefault(user_id, [])
            new_entry = dict(instance_data)
            new_entry[NUDGE_KEY] = _default_nudge()
            users[user_id].append(new_entry)
            self._write_db(data)
            return True

    def get_user_instances(self, user_id: str) -> List[Dict[str, Any]]:
        return self._read_db()["users"].get(user_id, [])

    def get_instance(self, user_id: str, instance_id: int) -> Optional[Dict[str, Any]]:
        for instance in self.get_user_instances(user_id):
            if instance.get("id") == instance_id:
                return instance
        return None

    def find_owner_of_instance(self, instance_id: int) -> Optional[str]:
        """Return the user_id tracking the given instance, or None."""
        for user_id, instances in self._read_db()["users"].items():
            for inst in instances:
                if inst.get("id") == instance_id:
                    return user_id
        return None

    async def remove_instance(self, user_id: str, instance_id: int) -> bool:
        async with self._lock:
            data = self._read_db()
            users = data["users"]
            if user_id not in users:
                return False

            for i, instance in enumerate(users[user_id]):
                if instance.get("id") == instance_id:
                    users[user_id].pop(i)
                    self._write_db(data)
                    return True
            return False

    async def update_instance(
        self, user_id: str, instance_id: int, instance_data: Dict[str, Any]
    ) -> bool:
        """Replace the raw Linode fields but preserve our nudge metadata."""
        async with self._lock:
            data = self._read_db()
            users = data["users"]
            if user_id not in users:
                return False

            for i, instance in enumerate(users[user_id]):
                if instance.get("id") == instance_id:
                    merged = dict(instance_data)
                    merged[NUDGE_KEY] = instance.get(NUDGE_KEY, _default_nudge())
                    users[user_id][i] = merged
                    self._write_db(data)
                    return True
            return False

    def get_all_instances(self) -> List[tuple]:
        all_instances = []
        for user_id, instances in self._read_db()["users"].items():
            for instance in instances:
                instance_id = instance.get("id")
                if instance_id:
                    all_instances.append((user_id, instance_id))
        return all_instances

    def iter_all_full(self):
        """Yield (user_id, instance_dict) for every stored instance."""
        for user_id, instances in self._read_db()["users"].items():
            for instance in instances:
                yield user_id, instance

    # ----- nudge state -----

    async def update_nudge(
        self, user_id: str, instance_id: int, **fields
    ) -> bool:
        async with self._lock:
            data = self._read_db()
            users = data["users"]
            if user_id not in users:
                return False

            for i, instance in enumerate(users[user_id]):
                if instance.get("id") == instance_id:
                    nudge = instance.get(NUDGE_KEY) or _default_nudge()
                    nudge.update(fields)
                    instance[NUDGE_KEY] = nudge
                    self._write_db(data)
                    return True
            return False

    def get_nudge(self, user_id: str, instance_id: int) -> Optional[Dict[str, Any]]:
        instance = self.get_instance(user_id, instance_id)
        if instance is None:
            return None
        return instance.get(NUDGE_KEY) or _default_nudge()
