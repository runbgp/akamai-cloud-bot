import json
import os
from typing import Dict, List, Optional, Any

class Database:
    """Simple JSON-based database to store user-specific Akamai Cloud instance information."""
    
    def __init__(self, db_file: str = "akamai_instances.json"):
        """Initialize the database with a file path."""
        self.db_file = db_file
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """Ensure the database file exists."""
        if not os.path.exists(self.db_file):
            with open(self.db_file, "w") as f:
                json.dump({}, f)
    
    def _read_db(self) -> Dict[str, Any]:
        """Read the database file."""
        with open(self.db_file, "r") as f:
            return json.load(f)
    
    def _write_db(self, data: Dict[str, Any]):
        """Write to the database file."""
        with open(self.db_file, "w") as f:
            json.dump(data, f, indent=2)
    
    def add_instance(self, user_id: str, instance_data: Dict[str, Any]):
        """
        Add an Akamai Cloud instance to the database.
        
        Args:
            user_id: Discord user ID
            instance_data: Akamai Cloud instance data
        """
        db = self._read_db()
        
        if user_id not in db:
            db[user_id] = []
        
        # Check if instance already exists
        for i, instance in enumerate(db[user_id]):
            if instance.get("id") == instance_data.get("id"):
                db[user_id][i] = instance_data
                self._write_db(db)
                return
        
        # Add new instance
        db[user_id].append(instance_data)
        self._write_db(db)
    
    def get_user_instances(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Get all Akamai Cloud instances for a user.
        
        Args:
            user_id: Discord user ID
            
        Returns:
            List of Akamai Cloud instances
        """
        db = self._read_db()
        return db.get(user_id, [])
    
    def get_instance(self, user_id: str, instance_id: int) -> Optional[Dict[str, Any]]:
        """
        Get a specific Akamai Cloud instance for a user.
        
        Args:
            user_id: Discord user ID
            instance_id: Akamai Cloud instance ID
            
        Returns:
            Akamai Cloud instance data or None if not found
        """
        instances = self.get_user_instances(user_id)
        for instance in instances:
            if instance.get("id") == instance_id:
                return instance
        return None
    
    def remove_instance(self, user_id: str, instance_id: int) -> bool:
        """
        Remove an Akamai Cloud instance from the database.
        
        Args:
            user_id: Discord user ID
            instance_id: Akamai Cloud instance ID
            
        Returns:
            True if instance was removed, False otherwise
        """
        db = self._read_db()
        
        if user_id not in db:
            return False
        
        for i, instance in enumerate(db[user_id]):
            if instance.get("id") == instance_id:
                db[user_id].pop(i)
                self._write_db(db)
                return True
        
        return False
    
    def update_instance(self, user_id: str, instance_id: int, instance_data: Dict[str, Any]) -> bool:
        """
        Update an Akamai Cloud instance in the database.
        
        Args:
            user_id: Discord user ID
            instance_id: Akamai Cloud instance ID
            instance_data: Updated Akamai Cloud instance data
            
        Returns:
            True if instance was updated, False otherwise
        """
        db = self._read_db()
        
        if user_id not in db:
            return False
        
        for i, instance in enumerate(db[user_id]):
            if instance.get("id") == instance_id:
                db[user_id][i] = instance_data
                self._write_db(db)
                return True
        
        return False
    
    def get_all_instances(self) -> List[tuple]:
        """
        Get all Akamai Cloud instances from all users.
        
        Returns:
            List of tuples containing (user_id, instance_id)
        """
        db = self._read_db()
        all_instances = []
        
        for user_id, instances in db.items():
            for instance in instances:
                instance_id = instance.get("id")
                if instance_id:
                    all_instances.append((user_id, instance_id))
        
        return all_instances