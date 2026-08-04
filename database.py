import asyncio
import json
import os
import datetime
from contextlib import asynccontextmanager
from typing import Dict, List, Optional, Any

# Keys for bot-managed data.
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
    """Store Akamai Cloud instances and cleanup state in a JSON file.

    The file supports these layouts:

      Legacy: {user_id: [instance, ...]}
      Current: {"_meta": {...}, "users": {user_id: [instance, ...]}}

    The first read converts the legacy layout.

    Public methods that change data use `self._lock`. This lock prevents concurrent tasks from overwriting changes.

    Read methods use one snapshot. This read is atomic on the single-threaded event loop.
    """

    def __init__(self, db_file: str = "akamai_instances.json"):
        self.db_file = db_file
        self._ensure_db_exists()
        # The lock binds to the active event loop on its first use.
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self):
        """Hold the database lock across related operations.

        If an operation must read and then write atomically, use this context manager.

        Inside a `transaction()` block, call only the private `_locked` methods. The lock is not reentrant.
        """
        async with self._lock:
            yield

    def _ensure_db_exists(self):
        # Initialize missing or empty files. A bind mount can create an empty file.
        if not os.path.exists(self.db_file) or os.path.getsize(self.db_file) == 0:
            with open(self.db_file, "w") as f:
                json.dump({"_meta": {}, "users": {}}, f)

    def _read_db(self) -> Dict[str, Any]:
        with open(self.db_file, "r") as f:
            data = json.load(f)

        if "users" not in data:
            # Convert the legacy layout with user IDs at the top level.
            data = {"_meta": {}, "users": data}
        data.setdefault("_meta", {})
        data.setdefault("users", {})
        return data

    def _write_db(self, data: Dict[str, Any]):
        with open(self.db_file, "w") as f:
            json.dump(data, f, indent=2)

    # Bot metadata

    def get_meta(self) -> Dict[str, Any]:
        return self._read_db().get("_meta", {})

    async def set_meta(self, **kwargs):
        async with self._lock:
            data = self._read_db()
            data["_meta"].update(kwargs)
            self._write_db(data)

    # Instances

    async def add_instance(self, user_id: str, instance_data: Dict[str, Any]):
        """Add or replace an instance for `user_id`.

        This method does not enforce cross-user uniqueness. Use `import_instance` for user imports.
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
        """If no user tracks the instance ID, add the instance.

        Return `True` after an add. If a user already tracks the instance, return `False`.
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
        """Return the user ID that tracks the instance, or `None`."""
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
        """Replace the Linode fields and keep the cleanup metadata."""
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
        """Yield the user ID and data for each stored instance."""
        for user_id, instances in self._read_db()["users"].items():
            for instance in instances:
                yield user_id, instance

    # Cleanup state

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
