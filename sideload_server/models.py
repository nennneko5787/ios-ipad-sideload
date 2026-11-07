"""
Data models for the iOS Sideload Server.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from enum import Enum


class PairingType(str, Enum):
    LOCKDOWN = "lockdown"
    REMOTE = "remote"  # RPPairing (iOS 17+)
    WIFI_ONBOARDING = "wifi_onboarding"  # iOS 27+ Wi-Fi onboarding


class PairingStatus(str, Enum):
    WAITING = "waiting"
    PIN_REQUIRED = "pin_required"
    PAIRING = "pairing"
    SUCCESS = "success"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class DeviceInfo:
    udid: str
    name: str
    model: str
    ios_version: str
    pairing_type: PairingType = PairingType.LOCKDOWN
    pairing_file_path: str = ""
    last_seen: datetime = field(default_factory=datetime.now)
    is_connected: bool = False
    device_info: Dict[str, Any] = field(default_factory=dict)
    
    def dict(self) -> Dict[str, Any]:
        return {
            "udid": self.udid,
            "name": self.name,
            "model": self.model,
            "ios_version": self.ios_version,
            "pairing_type": self.pairing_type.value,
            "pairing_file_path": self.pairing_file_path,
            "last_seen": self.last_seen.isoformat(),
            "is_connected": self.is_connected,
            "device_info": self.device_info,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DeviceInfo":
        data = data.copy()
        data["pairing_type"] = PairingType(data.get("pairing_type", "lockdown"))
        data["last_seen"] = datetime.fromisoformat(data["last_seen"])
        return cls(**data)


@dataclass
class AppInfo:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    bundle_id: str = ""
    device_udid: str = ""
    sign_config_id: str = ""
    original_ipa_path: str = ""
    signed_ipa_path: str = ""
    installed_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=datetime.now)
    last_installed: Optional[datetime] = None
    version: str = "1.0"
    short_version: str = "1.0"
    
    def dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "bundle_id": self.bundle_id,
            "device_udid": self.device_udid,
            "sign_config_id": self.sign_config_id,
            "original_ipa_path": self.original_ipa_path,
            "signed_ipa_path": self.signed_ipa_path,
            "installed_at": self.installed_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "last_installed": self.last_installed.isoformat() if self.last_installed else None,
            "version": self.version,
            "short_version": self.short_version,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppInfo":
        data = data.copy()
        for field_name in ["installed_at", "expires_at", "last_installed"]:
            if data.get(field_name):
                data[field_name] = datetime.fromisoformat(data[field_name])
        return cls(**data)


@dataclass
class SignConfig:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    cert_path: str = ""        # Path to .p12 certificate
    cert_password: str = ""    # Password for .p12
    profile_path: str = ""     # Path to .mobileprovision
    team_id: str = ""          # Apple Team ID
    bundle_id_prefix: str = "" # For wildcard profiles
    use_apple_id: bool = False # Use Apple ID auth instead of cert
    apple_id: str = ""         # Apple ID email
    apple_id_password: str = ""# Apple ID password (or app-specific)
    anisette_server: str = ""  # Custom anisette server URL
    is_valid: bool = False
    last_tested: Optional[datetime] = None
    
    def dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "cert_path": self.cert_path,
            "cert_password": "***",  # Don't expose password
            "profile_path": self.profile_path,
            "team_id": self.team_id,
            "bundle_id_prefix": self.bundle_id_prefix,
            "use_apple_id": self.use_apple_id,
            "apple_id": self.apple_id,
            "apple_id_password": "***",
            "anisette_server": self.anisette_server,
            "is_valid": self.is_valid,
            "last_tested": self.last_tested.isoformat() if self.last_tested else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SignConfig":
        data = data.copy()
        if data.get("last_tested"):
            data["last_tested"] = datetime.fromisoformat(data["last_tested"])
        return cls(**data)


@dataclass
class PairingRequest:
    device_name: str = "Sideload Server"
    pin: str = "000000"
    pairing_type: PairingType = PairingType.WIFI_ONBOARDING


@dataclass
class InstallRequest:
    app_id: str
    device_udid: str
    force_reinstall: bool = False


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: str = "./data"
    upload_dir: str = "./uploads"
    zsign_path: str = "zsign"
    mDNS_name: str = "sideload-server"
    auto_refresh_enabled: bool = True
    refresh_check_interval_hours: int = 1
    max_concurrent_installs: int = 2
    
    def dict(self) -> Dict[str, Any]:
        return {
            "host": self.host,
            "port": self.port,
            "data_dir": self.data_dir,
            "upload_dir": self.upload_dir,
            "zsign_path": self.zsign_path,
            "mDNS_name": self.mDNS_name,
            "auto_refresh_enabled": self.auto_refresh_enabled,
            "refresh_check_interval_hours": self.refresh_check_interval_hours,
            "max_concurrent_installs": self.max_concurrent_installs,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ServerConfig":
        return cls(**data)


@dataclass
class PairingSession:
    id: str
    device_name: str
    pin: str
    status: PairingStatus = PairingStatus.WAITING
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=lambda: datetime.now() + timedelta(minutes=5))
    device_udid: Optional[str] = None
    pairing_file_path: Optional[str] = None
    error: Optional[str] = None
    
    def dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "device_name": self.device_name,
            "pin": self.pin,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "expires_at": self.expires_at.isoformat(),
            "device_udid": self.device_udid,
            "pairing_file_path": self.pairing_file_path,
            "error": self.error,
        }