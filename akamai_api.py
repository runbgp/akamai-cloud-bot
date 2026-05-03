import os
import requests
from typing import Dict, List, Any


class AkamaiCloudAPI:
    """Class to interact with the Akamai Cloud API."""

    BASE_URL = "https://api.linode.com/v4"

    def __init__(self, api_token: str = None):
        """Initialize the AkamaiCloudAPI class with an API token."""
        self.api_token = api_token or os.getenv("AKAMAI_API_TOKEN")
        if not self.api_token:
            raise ValueError("Akamai Cloud API token is required")

        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    def get_regions(self) -> List[Dict[str, Any]]:
        """Get available Akamai Cloud regions."""
        response = requests.get(f"{self.BASE_URL}/regions", headers=self.headers)
        response.raise_for_status()
        return response.json().get("data", [])

    def get_images(self) -> List[Dict[str, Any]]:
        """Get available Akamai Cloud images (OS options)."""
        response = requests.get(f"{self.BASE_URL}/images", headers=self.headers)
        response.raise_for_status()
        return response.json().get("data", [])

    def get_instance_types(self) -> List[Dict[str, Any]]:
        """Get available Akamai Cloud instance types."""
        response = requests.get(f"{self.BASE_URL}/linode/types", headers=self.headers)
        response.raise_for_status()
        return response.json().get("data", [])

    def create_instance(
        self,
        label: str,
        region: str,
        image: str,
        root_pass: str,
        type: str = "g6-nanode-1",
    ) -> Dict[str, Any]:
        """
        Create a new Akamai Cloud instance.

        Args:
            label: A unique label for the instance
            region: The region ID (e.g., 'us-east')
            image: The image ID (e.g., 'linode/ubuntu20.04')
            root_pass: The root password for the instance
            type: The instance type (default: g6-nanode-1)

        Returns:
            Dict containing the created instance details
        """
        payload = {
            "label": label,
            "region": region,
            "image": image,
            "root_pass": root_pass,
            "type": type,
            "booted": True,
        }

        response = requests.post(
            f"{self.BASE_URL}/linode/instances", headers=self.headers, json=payload
        )
        response.raise_for_status()
        return response.json()

    def get_instances(self) -> List[Dict[str, Any]]:
        """Get all Akamai Cloud instances."""
        response = requests.get(
            f"{self.BASE_URL}/linode/instances", headers=self.headers
        )
        response.raise_for_status()
        return response.json().get("data", [])

    def get_instance(self, instance_id: int) -> Dict[str, Any]:
        """Get details for a specific Akamai Cloud instance."""
        response = requests.get(
            f"{self.BASE_URL}/linode/instances/{instance_id}", headers=self.headers
        )
        response.raise_for_status()
        return response.json()

    def delete_instance(self, instance_id: int) -> bool:
        """Delete an Akamai Cloud instance."""
        response = requests.delete(
            f"{self.BASE_URL}/linode/instances/{instance_id}", headers=self.headers
        )
        return response.status_code == 200

    def reboot_instance(self, instance_id: int) -> Dict[str, Any]:
        """Reboot an Akamai Cloud instance."""
        response = requests.post(
            f"{self.BASE_URL}/linode/instances/{instance_id}/reboot",
            headers=self.headers,
        )
        response.raise_for_status()
        return response.json()
