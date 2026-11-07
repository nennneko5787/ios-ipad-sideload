"""
Storage management for the iOS Sideload Server.
Handles persistence of config, devices, apps, and signing configs.
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from sideload_server.models import (
    AppInfo, DeviceInfo, ServerConfig, SignConfig
)


class StorageManager:
    def __init__(self, data_dir: str = "./data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.config_file = self.data_dir / "config.json"
        self.devices_file = self.data_dir / "devices.json"
        self.apps_file = self.data_dir / "apps.json"
        self.sign_configs_file = self.data_dir / "sign_configs.json"
        self.pairing_files_dir = self.data_dir / "pairing_files"
        self.pairing_files_dir.mkdir(parents=True, exist_ok=True)
    
    async def initialize(self):
        """Initialize storage directory and files."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.pairing_files_dir.mkdir(parents=True, exist_ok=True)
        
        # Create default files if they don't exist
        for file_path, default_content in [
            (self.config_file, {}),
            (self.devices_file, []),
            (self.apps_file, []),
            (self.sign_configs_file, []),
        ]:
            if not file_path.exists():
                file_path.write_text(json.dumps(default_content, indent=2))
    
    # Config
    async def save_config(self, config: ServerConfig):
        self.config_file.write_text(json.dumps(config.dict(), indent=2))
    
    async def load_config(self) -> Optional[ServerConfig]:
        if not self.config_file.exists():
            return None
        try:
            data = json.loads(self.config_file.read_text())
            if not data:
                return None
            return ServerConfig.from_dict(data)
        except Exception:
            return None
    
    # Devices
    async def save_devices(self, devices: List[DeviceInfo]):
        data = [d.dict() for d in devices]
        self.devices_file.write_text(json.dumps(data, indent=2))
    
    async def load_devices(self) -> List[DeviceInfo]:
        if not self.devices_file.exists():
            return []
        try:
            data = json.loads(self.devices_file.read_text())
            return [DeviceInfo.from_dict(d) for d in data]
        except Exception:
            return []
    
    # Apps
    async def save_apps(self, apps: List[AppInfo]):
        data = [a.dict() for a in apps]
        self.apps_file.write_text(json.dumps(data, indent=2))
    
    async def load_apps(self) -> List[AppInfo]:
        if not self.apps_file.exists():
            return []
        try:
            data = json.loads(self.apps_file.read_text())
            return [AppInfo.from_dict(d) for d in data]
        except Exception:
            return []
    
    # Signing configs
    async def save_sign_configs(self, configs: List[SignConfig]):
        # Don't save passwords in plain text - they're masked in dict()
        # We need to save the full data including passwords
        data = []
        for c in configs:
            config_dict = c.dict()
            # Note: In production, encrypt these!
            config_dict["cert_password"] = c.cert_password
            config_dict["apple_id_password"] = c.apple_id_password
            data.append(config_dict)
        self.sign_configs_file.write_text(json.dumps(data, indent=2))
    
    async def load_sign_configs(self) -> List[SignConfig]:
        if not self.sign_configs_file.exists():
            return []
        try:
            data = json.loads(self.sign_configs_file.read_text())
            configs = []
            for d in data:
                # Restore passwords
                if "cert_password" in d and d["cert_password"] != "***":
                    pass  # Already correct
                configs.append(SignConfig.from_dict(d))
            return configs
        except Exception:
            return []
    
    # Pairing files
    def get_pairing_file_path(self, udid: str) -> Path:
        return self.pairing_files_dir / f"{udid}.plist"
    
    def save_pairing_file(self, udid: str, content: bytes) -> Path:
        path = self.get_pairing_file_path(udid)
        path.write_bytes(content)
        return path
    
    def load_pairing_file(self, udid: str) -> Optional[bytes]:
        path = self.get_pairing_file_path(udid)
        if path.exists():
            return path.read_bytes()
        return None
    
    def delete_pairing_file(self, udid: str):
        path = self.get_pairing_file_path(udid)
        path.unlink(missing_ok=True)
    
    async def save_all(
        self,
        config: ServerConfig,
        devices: List[DeviceInfo],
        sign_configs: List[SignConfig],
        apps: List[AppInfo]
    ):
        await self.save_config(config)
        await self.save_devices(devices)
        await self.save_sign_configs(sign_configs)
        await self.save_apps(apps)