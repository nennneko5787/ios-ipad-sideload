"""
Signing management for iOS Sideload Server.
Handles IPA signing using zsign with certificates and provisioning profiles.
"""

import asyncio
import os
import plistlib
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from sideload_server.models import SignConfig
from sideload_server.storage import StorageManager


class SigningManager:
    def __init__(self, storage: StorageManager, zsign_path: str = "zsign"):
        self.storage = storage
        self.zsign_path = zsign_path
        self.configs: Dict[str, SignConfig] = {}
        self._signed_cache: Dict[str, str] = {}  # Cache signed IPAs
    
    def add_config(self, config: SignConfig):
        self.configs[config.id] = config
    
    def remove_config(self, config_id: str):
        if config_id in self.configs:
            del self.configs[config_id]
    
    # ===========================
    # Bundle ID Extraction
    # ===========================
    async def get_bundle_id(self, ipa_path: str) -> str:
        """Extract bundle ID from IPA."""
        try:
            with zipfile.ZipFile(ipa_path, 'r') as z:
                for name in z.namelist():
                    if name.endswith('.app/Info.plist'):
                        with z.open(name) as f:
                            plist_data = f.read()
                            plist = plistlib.loads(plist_data)
                            return plist.get('CFBundleIdentifier', '')
        except Exception as e:
            raise RuntimeError(f"Failed to extract bundle ID: {e}")
        return ""
    
    # ===========================
    # Signing with zsign
    # ===========================
    async def sign_ipa(
        self,
        ipa_path: str,
        config: SignConfig,
        bundle_id: str,
        device_udid: str
    ) -> str:
        """
        Sign an IPA with the given configuration.
        Returns path to signed IPA.
        """
        # Check cache
        cache_key = f"{ipa_path}:{config.id}:{bundle_id}:{device_udid}"
        if cache_key in self._signed_cache:
            cached = self._signed_cache[cache_key]
            if Path(cached).exists():
                return cached
        
        if config.use_apple_id:
            return await self._sign_with_apple_id(ipa_path, config, bundle_id, device_udid)
        else:
            return await self._sign_with_certificate(ipa_path, config, bundle_id, device_udid)
    
    async def _sign_with_certificate(
        self,
        ipa_path: str,
        config: SignConfig,
        bundle_id: str,
        device_udid: str
    ) -> str:
        """Sign using .p12 certificate and .mobileprovision profile."""
        if not config.cert_path or not config.profile_path:
            raise ValueError("Certificate and provisioning profile required")
        
        cert_path = Path(config.cert_path)
        profile_path = Path(config.profile_path)
        
        if not cert_path.exists():
            raise FileNotFoundError(f"Certificate not found: {cert_path}")
        if not profile_path.exists():
            raise FileNotFoundError(f"Provisioning profile not found: {profile_path}")
        
        # Create output path
        output_dir = Path(tempfile.gettempdir()) / "sideload_signed"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_name = f"{uuid.uuid4()}_{Path(ipa_path).name}"
        output_path = output_dir / output_name
        
        # Build zsign command
        cmd = [
            self.zsign_path,
            "-k", str(cert_path),
            "-p", config.cert_password,
            "-m", str(profile_path),
            "-b", bundle_id,
            "-o", str(output_path),
            ipa_path
        ]
        
        # Add entitlements if needed
        # zsign can auto-extract from profile
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            
            if proc.returncode != 0:
                error = stderr.decode().strip()
                raise RuntimeError(f"zsign failed: {error}")
            
            if not output_path.exists():
                raise RuntimeError("Signed IPA not created")
            
            self._signed_cache[cache_key] = str(output_path)
            return str(output_path)
            
        except asyncio.TimeoutError:
            raise RuntimeError("Signing timed out")
        except FileNotFoundError:
            raise RuntimeError(f"zsign not found at {self.zsign_path}. Please install zsign.")
    
    async def _sign_with_apple_id(
        self,
        ipa_path: str,
        config: SignConfig,
        bundle_id: str,
        device_udid: str
    ) -> str:
        """
        Sign using Apple ID (requires anisette).
        This uses pymobiledevice3's signing capabilities or zsign with auto-provisioning.
        """
        # For Apple ID signing, we need to:
        # 1. Login to Apple Developer Portal (via anisette)
        # 2. Register device UDID
        # 3. Create/get certificate
        # 4. Create/get provisioning profile
        # 5. Sign with zsign
        
        # This is complex - for now, use pymobiledevice3's sign command
        # which can handle Apple ID auth with anisette
        output_dir = Path(tempfile.gettempdir()) / "sideload_signed"
        output_dir.mkdir(parents=True, exist_ok=True)
        
        output_name = f"{uuid.uuid4()}_{Path(ipa_path).name}"
        output_path = output_dir / output_name
        
        # Use pymobiledevice3 sign command
        cmd = [
            "pymobiledevice3", "sign",
            ipa_path,
            "-o", str(output_path),
            "-b", bundle_id,
            "--apple-id", config.apple_id,
        ]
        
        if config.apple_id_password:
            cmd.extend(["--password", config.apple_id_password])
        
        if config.anisette_server:
            cmd.extend(["--anisette-server", config.anisette_server])
        
        # Device UDID for provisioning profile
        cmd.extend(["--device", device_udid])
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            
            if proc.returncode != 0:
                error = stderr.decode().strip()
                raise RuntimeError(f"Apple ID signing failed: {error}")
            
            if not output_path.exists():
                raise RuntimeError("Signed IPA not created")
            
            self._signed_cache[cache_key] = str(output_path)
            return str(output_path)
            
        except asyncio.TimeoutError:
            raise RuntimeError("Apple ID signing timed out")
        except FileNotFoundError:
            raise RuntimeError("pymobiledevice3 not found")
    
    # ===========================
    # Provisioning Profile Info
    # ===========================
    def parse_provisioning_profile(self, profile_path: str) -> Dict[str, Any]:
        """Parse .mobileprovision file to extract info."""
        try:
            # mobileprovision is a CMS signed plist
            # Use security command on macOS or openssl to decode
            # For cross-platform, use Python's cryptography or shell out
            result = subprocess.run(
                ["security", "cms", "-D", "-i", profile_path],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                plist = plistlib.loads(result.stdout.encode())
                return {
                    "name": plist.get("Name", ""),
                    "uuid": plist.get("UUID", ""),
                    "team_id": plist.get("TeamIdentifier", [""])[0] if plist.get("TeamIdentifier") else "",
                    "bundle_id": plist.get("Entitlements", {}).get("application-identifier", "").split(".", 1)[-1],
                    "expiration": plist.get("ExpirationDate"),
                    "devices": plist.get("ProvisionedDevices", []),
                    "certificates": len(plist.get("DeveloperCertificates", [])),
                }
        except Exception:
            pass
        
        # Fallback: try openssl
        try:
            result = subprocess.run(
                ["openssl", "smime", "-verify", "-inform", "der", "-in", profile_path, "-noverify"],
                capture_output=True, timeout=10
            )
            if result.returncode == 0:
                plist = plistlib.loads(result.stdout)
                return {
                    "name": plist.get("Name", ""),
                    "uuid": plist.get("UUID", ""),
                    "team_id": plist.get("TeamIdentifier", [""])[0] if plist.get("TeamIdentifier") else "",
                    "bundle_id": plist.get("Entitlements", {}).get("application-identifier", "").split(".", 1)[-1],
                    "expiration": plist.get("ExpirationDate"),
                }
        except Exception:
            pass
        
        return {}
    
    # ===========================
    # Config Testing
    # ===========================
    async def test_config(self, config: SignConfig) -> Dict[str, Any]:
        """Test a signing configuration."""
        result = {
            "valid": False,
            "certificate_valid": False,
            "profile_valid": False,
            "profile_info": {},
            "errors": []
        }
        
        if config.use_apple_id:
            # Test Apple ID login
            try:
                cmd = ["pymobiledevice3", "account", "login"]
                if config.anisette_server:
                    cmd.extend(["--anisette-server", config.anisette_server])
                cmd.extend(["--apple-id", config.apple_id])
                if config.apple_id_password:
                    cmd.extend(["--password", config.apple_id_password])
                
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                
                if proc.returncode == 0:
                    result["valid"] = True
                    result["certificate_valid"] = True
                else:
                    result["errors"].append(stderr.decode().strip())
            except Exception as e:
                result["errors"].append(str(e))
        else:
            # Test certificate and profile
            if config.cert_path and Path(config.cert_path).exists():
                # Test p12
                try:
                    cmd = ["openssl", "pkcs12", "-in", config.cert_path, "-nokeys", "-passin", f"pass:{config.cert_password}"]
                    proc = await asyncio.create_subprocess_exec(
                        *cmd,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=10)
                    result["certificate_valid"] = proc.returncode == 0
                    if proc.returncode != 0:
                        result["errors"].append("Invalid certificate or password")
                except Exception:
                    result["errors"].append("Failed to verify certificate")
            else:
                result["errors"].append("Certificate file not found")
            
            if config.profile_path and Path(config.profile_path).exists():
                profile_info = self.parse_provisioning_profile(config.profile_path)
                result["profile_info"] = profile_info
                result["profile_valid"] = bool(profile_info)
                if not result["profile_valid"]:
                    result["errors"].append("Invalid provisioning profile")
            else:
                result["errors"].append("Provisioning profile not found")
            
            result["valid"] = result["certificate_valid"] and result["profile_valid"]
        
        config.is_valid = result["valid"]
        config.last_tested = datetime.now()
        return result
    
    # ===========================
    # Cleanup
    # ===========================
    def clear_cache(self):
        """Clear signed IPA cache."""
        for path in self._signed_cache.values():
            Path(path).unlink(missing_ok=True)
        self._signed_cache.clear()