"""
Device management for iOS Sideload Server.
Handles device pairing (RPPairing/Wi-Fi onboarding), connection, and app installation.
Uses pymobiledevice3 for device communication.
"""

import asyncio
import json
import plistlib
import subprocess
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sideload_server.models import (
    AppInfo, DeviceInfo, PairingSession, PairingStatus, PairingType
)
from sideload_server.storage import StorageManager


class DeviceManager:
    def __init__(self, storage: StorageManager, zsign_path: str = "zsign"):
        self.storage = storage
        self.zsign_path = zsign_path
        self.devices: Dict[str, DeviceInfo] = {}
        self.apps: Dict[str, AppInfo] = {}
        self.pairing_sessions: Dict[str, PairingSession] = {}
        self._pairing_processes: Dict[str, subprocess.Popen] = {}
    
    def add_device(self, device: DeviceInfo):
        self.devices[device.udid] = device
    
    def remove_device(self, udid: str):
        if udid in self.devices:
            del self.devices[udid]
        self.storage.delete_pairing_file(udid)
    
    def add_app(self, app: AppInfo):
        self.apps[app.id] = app
    
    def remove_app(self, app_id: str):
        if app_id in self.apps:
            del self.apps[app_id]
    
    def get_expiring_apps(self, days: int = 7) -> List[AppInfo]:
        from datetime import timedelta
        threshold = datetime.now() + timedelta(days=days)
        return [
            app for app in self.apps.values()
            if app.expires_at <= threshold
        ]
    
    # ===========================
    # pymobiledevice3 Helpers
    # ===========================
    async def _run_pymobiledevice3(self, args: List[str], timeout: int = 60) -> Dict[str, Any]:
        """Run pymobiledevice3 command and return parsed JSON output."""
        cmd = ["pymobiledevice3", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            
            if proc.returncode != 0:
                error_msg = stderr.decode().strip()
                raise RuntimeError(f"pymobiledevice3 failed: {error_msg}")
            
            output = stdout.decode().strip()
            if output:
                return json.loads(output)
            return {}
        except asyncio.TimeoutError:
            raise RuntimeError(f"Command timed out: {' '.join(cmd)}")
        except json.JSONDecodeError:
            # Some commands don't output JSON
            return {"raw_output": output}
    
    async def _run_pymobiledevice3_raw(self, args: List[str], timeout: int = 120) -> subprocess.CompletedProcess:
        """Run pymobiledevice3 and return raw result."""
        cmd = ["pymobiledevice3", *args]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
    
    # ===========================
    # Wi-Fi Pairing (RPPairing / iOS 27+)
    # ===========================
    async def start_wifi_pairing(
        self,
        pairing_id: str,
        device_name: str = "Sideload Server",
        pin: str = "000000"
    ) -> Dict[str, Any]:
        """
        Start Wi-Fi pairing using iOS 27+ RPPairing onboarding.
        This uses pymobiledevice3's remote pairing feature.
        """
        session = PairingSession(
            id=pairing_id,
            device_name=device_name,
            pin=pin,
            status=PairingStatus.WAITING
        )
        self.pairing_sessions[pairing_id] = session
        
        # Start pairing in background
        asyncio.create_task(self._perform_wifi_pairing(pairing_id))
        
        return {"pairing_id": pairing_id, "status": "started"}
    
    async def _perform_wifi_pairing(self, pairing_id: str):
        """Perform the actual Wi-Fi pairing process."""
        session = self.pairing_sessions.get(pairing_id)
        if not session:
            return
        
        try:
            session.status = PairingStatus.PAIRING
            
            # Use pymobiledevice3 remote pair with Wi-Fi onboarding
            # This requires iOS 27+ on the device
            # The command will advertise via mDNS and wait for device to connect
            cmd = [
                "remote", "pair",
                "--device-name", session.device_name,
                "--pin", session.pin,
                "--output", str(self.storage.pairing_files_dir / f"pairing_{pairing_id}.plist")
            ]
            
            proc = await asyncio.create_subprocess_exec(
                "pymobiledevice3", *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            self._pairing_processes[pairing_id] = proc
            
            # Wait for pairing to complete
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            
            if proc.returncode == 0:
                # Pairing successful
                pairing_file = self.storage.pairing_files_dir / f"pairing_{pairing_id}.plist"
                if pairing_file.exists():
                    pairing_data = pairing_file.read_bytes()
                    
                    # Extract UDID from pairing file
                    plist = plistlib.loads(pairing_data)
                    udid = plist.get("DeviceID", "")
                    
                    session.status = PairingStatus.SUCCESS
                    session.device_udid = udid
                    session.pairing_file_path = str(pairing_file)
                    
                    # Create device info
                    device_info = await self.fetch_device_info(udid)
                    device = DeviceInfo(
                        udid=udid,
                        name=device_info.get("DeviceName", session.device_name),
                        model=device_info.get("ProductType", "Unknown"),
                        ios_version=device_info.get("ProductVersion", "Unknown"),
                        pairing_type=PairingType.WIFI_ONBOARDING,
                        pairing_file_path=str(pairing_file),
                        device_info=device_info,
                    )
                    
                    self.add_device(device)
                    await self.storage.save_devices(list(self.devices.values()))
                    
                    # Move pairing file to permanent location
                    permanent_path = self.storage.get_pairing_file_path(udid)
                    pairing_file.rename(permanent_path)
                    device.pairing_file_path = str(permanent_path)
                else:
                    session.status = PairingStatus.FAILED
                    session.error = "Pairing file not created"
            else:
                error_msg = stderr.decode().strip()
                session.status = PairingStatus.FAILED
                session.error = error_msg
                
                # Check if PIN is required
                if "pin" in error_msg.lower() or "passcode" in error_msg.lower():
                    session.status = PairingStatus.PIN_REQUIRED
        
        except asyncio.TimeoutError:
            session.status = PairingStatus.EXPIRED
            session.error = "Pairing timeout - ensure device is on same network and iOS 27+"
        except Exception as e:
            session.status = PairingStatus.FAILED
            session.error = str(e)
        finally:
            self._pairing_processes.pop(pairing_id, None)
    
    def get_pairing_status(self, pairing_id: Optional[str] = None) -> Dict[str, Any]:
        if pairing_id:
            session = self.pairing_sessions.get(pairing_id)
            return session.dict() if session else {}
        return {pid: s.dict() for pid, s in self.pairing_sessions.items()}
    
    async def confirm_wifi_pairing(self, pairing_id: str) -> Optional[DeviceInfo]:
        """Confirm pairing after user enters PIN on device."""
        session = self.pairing_sessions.get(pairing_id)
        if not session:
            return None
        
        # The pairing process should have completed in _perform_wifi_pairing
        # This method is called after user confirms on device
        if session.status == PairingStatus.SUCCESS and session.device_udid:
            return self.devices.get(session.device_udid)
        
        return None
    
    # ===========================
    # Device Info & Connection
    # ===========================
    async def fetch_device_info(self, udid: str) -> Dict[str, Any]:
        """Fetch device information using pymobiledevice3."""
        device = self.devices.get(udid)
        pairing_file = device.pairing_file_path if device else None
        
        args = ["lockdown", "get"]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        try:
            result = await self._run_pymobiledevice3(args)
            return result
        except Exception as e:
            return {"error": str(e)}
    
    async def check_device_connection(self, udid: str) -> bool:
        """Check if device is reachable."""
        try:
            info = await self.fetch_device_info(udid)
            device = self.devices.get(udid)
            if device:
                device.is_connected = "error" not in info
                device.last_seen = datetime.now()
            return "error" not in info
        except Exception:
            return False
    
    # ===========================
    # App Installation
    # ===========================
    async def install_app(
        self,
        udid: str,
        ipa_path: str,
        bundle_id: str
    ) -> bool:
        """Install an IPA on the device."""
        device = self.devices.get(udid)
        if not device:
            raise ValueError(f"Device {udid} not found")
        
        pairing_file = device.pairing_file_path
        
        args = ["apps", "install", ipa_path]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        try:
            result = await self._run_pymobiledevice3(args, timeout=300)
            return result.get("success", True)
        except Exception as e:
            print(f"Install failed: {e}")
            return False
    
    async def uninstall_app(self, udid: str, bundle_id: str) -> bool:
        """Uninstall an app from the device."""
        device = self.devices.get(udid)
        if not device:
            raise ValueError(f"Device {udid} not found")
        
        pairing_file = device.pairing_file_path
        
        args = ["apps", "uninstall", bundle_id]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        try:
            await self._run_pymobiledevice3(args)
            return True
        except Exception as e:
            print(f"Uninstall failed: {e}")
            return False
    
    async def list_installed_apps(self, udid: str) -> List[Dict[str, Any]]:
        """List installed apps on device."""
        device = self.devices.get(udid)
        if not device:
            return []
        
        pairing_file = device.pairing_file_path
        
        args = ["apps", "list"]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        try:
            result = await self._run_pymobiledevice3(args)
            return result.get("applications", [])
        except Exception:
            return []
    
    # ===========================
    # Developer Mode & Tunnel
    # ===========================
    async def enable_developer_mode(self, udid: str) -> bool:
        """Enable Developer Mode on device (required for sideloading)."""
        device = self.devices.get(udid)
        if not device:
            return False
        
        pairing_file = device.pairing_file_path
        
        args = ["devmode", "enable"]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        try:
            await self._run_pymobiledevice3(args)
            return True
        except Exception:
            return False
    
    async def start_tunnel(self, udid: str) -> bool:
        """Start the RSD tunnel for iOS 17+ devices."""
        device = self.devices.get(udid)
        if not device:
            return False
        
        pairing_file = device.pairing_file_path
        
        args = ["tunnel", "start"]
        if pairing_file:
            args.extend(["--pairing-file", pairing_file])
        else:
            args.extend(["--udid", udid])
        
        # Use userspace tunnel (no root/TUN required) - works on Termux
        args.extend(["--userspace"])
        
        try:
            # This runs in background
            proc = await asyncio.create_subprocess_exec(
                "pymobiledevice3", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Give it a moment to start
            await asyncio.sleep(2)
            return proc.returncode is None  # Still running
        except Exception:
            return False
    
    # ===========================
    # Pairing File Management
    # ===========================
    def load_pairing_file(self, udid: str) -> Optional[bytes]:
        return self.storage.load_pairing_file(udid)
    
    def save_pairing_file(self, udid: str, content: bytes) -> Path:
        return self.storage.save_pairing_file(udid, content)
    
    # ===========================
    # App Bundle ID Extraction
    # ===========================
    async def get_bundle_id_from_ipa(self, ipa_path: str) -> str:
        """Extract bundle ID from IPA using pymobiledevice3 or plist."""
        import zipfile
        import plistlib
        
        try:
            with zipfile.ZipFile(ipa_path, 'r') as z:
                # Find Info.plist
                for name in z.namelist():
                    if name.endswith('.app/Info.plist'):
                        with z.open(name) as f:
                            plist_data = f.read()
                            plist = plistlib.loads(plist_data)
                            return plist.get('CFBundleIdentifier', '')
        except Exception:
            pass
        
        # Fallback: use pymobiledevice3
        try:
            result = await self._run_pymobiledevice3(["apps", "info", ipa_path])
            return result.get("bundle_id", "")
        except Exception:
            return ""