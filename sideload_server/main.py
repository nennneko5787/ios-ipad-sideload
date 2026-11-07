#!/usr/bin/env python3
"""
iOS Sideload Server for Termux (armv7a) - TUI Version
A terminal-based sideloading tool for iOS 27+ using RPPairing (Wi-Fi pairing).
Supports IPA signing and installation over Wi-Fi without USB.
"""

import asyncio
import os
import signal
import sys
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, ScrollableContainer
from textual.content import Content
from textual.message import Message
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import (
    Button, DataTable, Footer, Header, Input, Label, 
    ListItem, ListView, Log, Markdown, Select, Static, TabbedContent, TabPane, 
    TextArea, Tree
)
from textual.widgets._tree import TreeNode

from sideload_server.device_manager import DeviceManager
from sideload_server.signing import SigningManager
from sideload_server.storage import StorageManager
from sideload_server.models import (
    DeviceInfo, AppInfo, SignConfig, ServerConfig, PairingStatus, PairingType
)


# ===========================
# Application State
# ===========================
@dataclass
class AppState:
    device_manager: DeviceManager = field(default_factory=DeviceManager)
    signing_manager: SigningManager = field(default_factory=SigningManager)
    storage: StorageManager = field(default_factory=StorageManager)
    config: ServerConfig = field(default_factory=ServerConfig)
    current_tab: str = "dashboard"
    selected_device_udid: Optional[str] = None
    selected_app_id: Optional[str] = None
    selected_sign_config_id: Optional[str] = None
    pairing_sessions: Dict[str, Any] = field(default_factory=dict)


app_state = AppState()


# ===========================
# Custom Messages
# ===========================
class DeviceListChanged(Message):
    pass

class AppListChanged(Message):
    pass

class SignConfigListChanged(Message):
    pass

class PairingStatusChanged(Message):
    def __init__(self, pairing_id: str, status: PairingStatus):
        super().__init__()
        self.pairing_id = pairing_id
        self.status = status


# ===========================
# Screens
# ===========================
class DashboardScreen(Screen):
    """Main dashboard screen."""
    
    BINDINGS = [
        Binding("d", "switch_tab('dashboard')", "Dashboard"),
        Binding("v", "switch_tab('devices')", "Devices"),
        Binding("a", "switch_tab('apps')", "Apps"),
        Binding("s", "switch_tab('signing')", "Signing"),
        Binding("c", "switch_tab('config')", "Config"),
        Binding("q", "quit", "Quit"),
    ]
    
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Container(id="main-container"):
            with TabbedContent(id="main-tabs", initial="dashboard"):
                with TabPane("Dashboard", id="dashboard"):
                    yield DashboardContent()
                with TabPane("Devices", id="devices"):
                    yield DevicesContent()
                with TabPane("Apps", id="apps"):
                    yield AppsContent()
                with TabPane("Signing", id="signing"):
                    yield SigningContent()
                with TabPane("Config", id="config"):
                    yield ConfigContent()
        yield Footer()
    
    def action_switch_tab(self, tab: str) -> None:
        tabs = self.query_one("#main-tabs", TabbedContent)
        tabs.active = tab
        app_state.current_tab = tab
    
    def action_quit(self) -> None:
        self.app.exit()


class DashboardContent(Static):
    """Dashboard overview content."""
    
    def compose(self) -> ComposeResult:
        with Container(classes="dashboard"):
            yield Static("📱 iOS Sideload Server", classes="title")
            yield Static("", id="status-bar")
            with Horizontal(classes="stats-row"):
                yield StatCard("Devices", "0", "📱", id="stat-devices")
                yield StatCard("Apps", "0", "📦", id="stat-apps")
                yield StatCard("Sign Configs", "0", "🔐", id="stat-signing")
                yield StatCard("Expiring Soon", "0", "⚠️", id="stat-expiring")
            yield Static("Recent Activity", classes="section-title")
            yield Log(id="activity-log", highlight=True, markup=True)
    
    def on_mount(self) -> None:
        self.update_stats()
        self.update_activity()
        # Refresh periodically
        self.set_interval(5, self.update_stats)
        self.set_interval(5, self.update_activity)
    
    def update_stats(self) -> None:
        devices_count = len(app_state.device_manager.devices)
        apps_count = len(app_state.device_manager.apps)
        signing_count = len(app_state.signing_manager.configs)
        expiring = len(app_state.device_manager.get_expiring_apps(3))
        
        self.query_one("#stat-devices", StatCard).update_value(str(devices_count))
        self.query_one("#stat-apps", StatCard).update_value(str(apps_count))
        self.query_one("#stat-signing", StatCard).update_value(str(signing_count))
        self.query_one("#stat-expiring", StatCard).update_value(str(expiring))
        
        # Status bar
        status = self.query_one("#status-bar", Static)
        pairing_sessions = len(app_state.pairing_sessions)
        if pairing_sessions:
            status.update(f"[yellow]⚡ {pairing_sessions} pairing session(s) active[/]")
        else:
            status.update("[green]● Ready[/]")
    
    def update_activity(self) -> None:
        log = self.query_one("#activity-log", Log)
        # Add recent activity - in real implementation, track events
        pass


class StatCard(Static):
    """A stat card widget."""
    
    def __init__(self, label: str, value: str, icon: str, **kwargs):
        super().__init__(**kwargs)
        self.label = label
        self.value = value
        self.icon = icon
    
    def compose(self) -> ComposeResult:
        yield Static(f"{self.icon} {self.value}", classes="stat-value", id="stat-value")
        yield Static(self.label, classes="stat-label")
    
    def update_value(self, value: str) -> None:
        self.value = value
        self.query_one("#stat-value", Static).update(f"{self.icon} {value}")


class DevicesContent(Static):
    """Devices management screen."""
    
    def compose(self) -> ComposeResult:
        with Container(classes="devices-screen"):
            with Horizontal(classes="toolbar"):
                yield Static("📱 Paired Devices", classes="section-title")
                yield Button("➕ Pair New Device", id="btn-pair", variant="primary")
                yield Button("🔄 Refresh", id="btn-refresh-devices")
            yield DataTable(id="devices-table", cursor_type="row")
            with Container(id="device-details", classes="details-panel"):
                yield Static("Select a device to see details", classes="placeholder")
    
    def on_mount(self) -> None:
        self.setup_table()
        self.refresh_devices()
    
    def setup_table(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        table.add_columns("Name", "Model", "iOS", "UDID", "Pairing Type", "Status", "Last Seen")
        table.zebra_stripes = True
    
    def refresh_devices(self) -> None:
        table = self.query_one("#devices-table", DataTable)
        table.clear()
        
        for device in app_state.device_manager.devices.values():
            status = "🟢 Online" if device.is_connected else "🔴 Offline"
            pairing_type = device.pairing_type.value.replace("_", " ").title()
            last_seen = device.last_seen.strftime("%m/%d %H:%M")
            
            table.add_row(
                device.name,
                device.model,
                device.ios_version,
                device.udid[:12] + "...",
                pairing_type,
                status,
                last_seen,
                key=device.udid
            )
    
    @on(DataTable.RowSelected)
    def on_device_selected(self, event: DataTable.RowSelected) -> None:
        udid = event.row_key.value
        app_state.selected_device_udid = udid
        self.show_device_details(udid)
    
    @on(Button.Pressed, "#btn-pair")
    async def on_pair_clicked(self) -> None:
        await self.start_pairing()
    
    @on(Button.Pressed, "#btn-refresh-devices")
    def on_refresh_clicked(self) -> None:
        self.refresh_devices()
        # Also check connections
        for udid in list(app_state.device_manager.devices.keys()):
            asyncio.create_task(app_state.device_manager.check_device_connection(udid))
        self.notify("Checking device connections...")
    
    def show_device_details(self, udid: str) -> None:
        device = app_state.device_manager.devices.get(udid)
        if not device:
            return
        
        details = self.query_one("#device-details", Container)
        details.remove_children()
        
        details.mount(
            Static(f"Device Details: {device.name}", classes="detail-title"),
            Static(f"UDID: {device.udid}"),
            Static(f"Model: {device.model}"),
            Static(f"iOS Version: {device.ios_version}"),
            Static(f"Pairing Type: {device.pairing_type.value}"),
            Static(f"Pairing File: {device.pairing_file_path or 'N/A'}"),
            Static(f"Last Seen: {device.last_seen.strftime('%Y-%m-%d %H:%M:%S')}"),
            Static(f"Status: {'Connected' if device.is_connected else 'Disconnected'}"),
            classes="device-detail"
        )
        
        # Action buttons
        with Horizontal(classes="detail-actions"):
            details.mount(
                Button("📋 Fetch Info", id=f"fetch-info-{udid}"),
                Button("🔧 Enable Dev Mode", id=f"devmode-{udid}"),
                Button("🌐 Start Tunnel", id=f"tunnel-{udid}"),
                Button("🗑️ Remove", id=f"remove-{udid}", variant="error"),
            )
    
    @on(Button.Pressed)
    async def on_device_action(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if not button_id:
            return
        
        if button_id.startswith("fetch-info-"):
            udid = button_id.replace("fetch-info-", "")
            await self.fetch_device_info(udid)
        elif button_id.startswith("devmode-"):
            udid = button_id.replace("devmode-", "")
            await self.enable_dev_mode(udid)
        elif button_id.startswith("tunnel-"):
            udid = button_id.replace("tunnel-", "")
            await self.start_tunnel(udid)
        elif button_id.startswith("remove-"):
            udid = button_id.replace("remove-", "")
            await self.remove_device(udid)
    
    async def start_pairing(self) -> None:
        """Start Wi-Fi pairing (RPPairing) for iOS 27+."""
        # Show input dialog for device name
        from textual.screen import ModalScreen
        from textual.widgets import Input
        
        class PairingInputScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Wi-Fi Pairing (iOS 27+)", classes="dialog-title")
                    yield Label("Device Name:")
                    yield Input(placeholder="Sideload Server", id="device-name")
                    yield Label("PIN (default: 000000):")
                    yield Input(placeholder="000000", id="pin")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Start Pairing", id="confirm", variant="primary")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#confirm")
            def confirm(self) -> None:
                name = self.query_one("#device-name", Input).value or "Sideload Server"
                pin = self.query_one("#pin", Input).value or "000000"
                self.dismiss((name, pin))
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(None)
        
        result = await self.app.push_screen_wait(PairingInputScreen())
        if result:
            name, pin = result
            pairing_id = await app_state.device_manager.start_wifi_pairing(
                name=name,
                pin=pin
            )
            app_state.pairing_sessions[pairing_id] = {
                "id": pairing_id,
                "name": name,
                "pin": pin,
                "status": PairingStatus.WAITING,
                "created_at": datetime.now()
            }
            self.notify(f"Pairing started: {pairing_id[:8]}... Check device for PIN prompt")
            self.show_pairing_status(pairing_id)
    
    def show_pairing_status(self, pairing_id: str) -> None:
        """Show pairing status in a modal."""
        class PairingStatusScreen(ModalScreen):
            def __init__(self, pairing_id: str):
                super().__init__()
                self.pairing_id = pairing_id
            
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Pairing Status", classes="dialog-title")
                    yield Static(f"Pairing ID: {self.pairing_id[:8]}...", id="pairing-id")
                    yield Static("Waiting for device...", id="pairing-status")
                    yield Static("On your iPhone/iPad (iOS 27+):", classes="instruction")
                    yield Static("1. Go to Settings → General → VPN & Device Management", classes="instruction")
                    yield Static("2. Tap 'Pair New Device' or similar", classes="instruction")
                    yield Static("3. Select this server and enter PIN", classes="instruction")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Check Status", id="check")
                        yield Button("Close", id="close")
            
            @on(Button.Pressed, "#check")
            async def check_status(self) -> None:
                status = app_state.device_manager.get_pairing_status(self.pairing_id)
                status_widget = self.query_one("#pairing-status", Static)
                if status:
                    s = status.get("status", "unknown")
                    status_widget.update(f"Status: {s}")
                    if s == "success":
                        status_widget.update("[green]✅ Pairing successful![/]")
                        self.notify("Device paired successfully!")
                        self.app.pop_screen()
                        # Refresh devices list
                        self.app.screen.query_one(DevicesContent).refresh_devices()
                else:
                    status_widget.update("Status: Unknown")
            
            @on(Button.Pressed, "#close")
            def close(self) -> None:
                self.dismiss()
        
        self.app.push_screen(PairingStatusScreen(pairing_id))
    
    async def fetch_device_info(self, udid: str) -> None:
        self.notify("Fetching device info...")
        info = await app_state.device_manager.fetch_device_info(udid)
        device = app_state.device_manager.devices.get(udid)
        if device:
            device.device_info = info
            await app_state.storage.save_devices(list(app_state.device_manager.devices.values()))
        self.show_device_details(udid)
        self.notify("Device info updated")
    
    async def enable_dev_mode(self, udid: str) -> None:
        self.notify("Enabling Developer Mode...")
        success = await app_state.device_manager.enable_developer_mode(udid)
        if success:
            self.notify("Developer Mode enabled!")
        else:
            self.notify("Failed to enable Developer Mode", severity="error")
    
    async def start_tunnel(self, udid: str) -> None:
        self.notify("Starting tunnel (userspace)...")
        success = await app_state.device_manager.start_tunnel(udid)
        if success:
            self.notify("Tunnel started!")
        else:
            self.notify("Failed to start tunnel", severity="error")
    
    async def remove_device(self, udid: str) -> None:
        # Confirm
        class ConfirmScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Remove Device?", classes="dialog-title")
                    yield Static(f"Remove {udid[:12]}...? This cannot be undone.")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Remove", id="confirm", variant="error")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#confirm")
            def confirm(self) -> None:
                self.dismiss(True)
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(False)
        
        confirmed = await self.app.push_screen_wait(ConfirmScreen())
        if confirmed:
            app_state.device_manager.remove_device(udid)
            await app_state.storage.save_devices(list(app_state.device_manager.devices.values()))
            self.refresh_devices()
            self.query_one("#device-details", Container).remove_children()
            self.query_one("#device-details", Container).mount(
                Static("Select a device to see details", classes="placeholder")
            )
            self.notify("Device removed")


class AppsContent(Static):
    """Apps management screen."""
    
    def compose(self) -> ComposeResult:
        with Container(classes="apps-screen"):
            with Horizontal(classes="toolbar"):
                yield Static("📦 Installed Apps", classes="section-title")
                yield Button("📥 Install IPA", id="btn-install", variant="primary")
                yield Button("🔄 Refresh", id="btn-refresh-apps")
            yield DataTable(id="apps-table", cursor_type="row")
            with Container(id="app-details", classes="details-panel"):
                yield Static("Select an app to see details", classes="placeholder")
    
    def on_mount(self) -> None:
        self.setup_table()
        self.refresh_apps()
        self.set_interval(30, self.refresh_apps)
    
    def setup_table(self) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.add_columns("Name", "Bundle ID", "Device", "Version", "Installed", "Expires", "Status")
        table.zebra_stripes = True
    
    def refresh_apps(self) -> None:
        table = self.query_one("#apps-table", DataTable)
        table.clear()
        
        for app in app_state.device_manager.apps.values():
            device = app_state.device_manager.devices.get(app.device_udid)
            device_name = device.name if device else app.device_udid[:8]
            
            now = datetime.now()
            if app.expires_at < now:
                status = "[red]Expired[/]"
                status_class = "expiry-expired"
            elif app.expires_at < now + timedelta(days=3):
                status = "[yellow]Expiring Soon[/]"
                status_class = "expiry-soon"
            else:
                status = "[green]Valid[/]"
                status_class = ""
            
            table.add_row(
                app.name,
                app.bundle_id,
                device_name,
                app.short_version,
                app.installed_at.strftime("%m/%d %H:%M"),
                app.expires_at.strftime("%m/%d %H:%M"),
                status,
                key=app.id
            )
    
    @on(DataTable.RowSelected)
    def on_app_selected(self, event: DataTable.RowSelected) -> None:
        app_id = event.row_key.value
        app_state.selected_app_id = app_id
        self.show_app_details(app_id)
    
    @on(Button.Pressed, "#btn-install")
    async def on_install_clicked(self) -> None:
        await self.install_ipa()
    
    @on(Button.Pressed, "#btn-refresh-apps")
    def on_refresh_clicked(self) -> None:
        self.refresh_apps()
    
    def show_app_details(self, app_id: str) -> None:
        app = app_state.device_manager.apps.get(app_id)
        if not app:
            return
        
        device = app_state.device_manager.devices.get(app.device_udid)
        sign_config = app_state.signing_manager.configs.get(app.sign_config_id)
        
        details = self.query_one("#app-details", Container)
        details.remove_children()
        
        now = datetime.now()
        if app.expires_at < now:
            expiry_style = "red"
            expiry_text = "EXPIRED"
        elif app.expires_at < now + timedelta(days=3):
            expiry_style = "yellow"
            expiry_text = f"Expires in {(app.expires_at - now).days} days"
        else:
            expiry_style = "green"
            expiry_text = f"Valid until {app.expires_at.strftime('%m/%d')}"
        
        details.mount(
            Static(f"App Details: {app.name}", classes="detail-title"),
            Static(f"Bundle ID: {app.bundle_id}"),
            Static(f"Device: {device.name if device else app.device_udid}"),
            Static(f"Signing Config: {sign_config.name if sign_config else 'Unknown'}"),
            Static(f"Version: {app.version} ({app.short_version})"),
            Static(f"Installed: {app.installed_at.strftime('%Y-%m-%d %H:%M')}"),
            Static(f"Expires: [{expiry_style}]{expiry_text}[/]"),
            Static(f"Original IPA: {app.original_ipa_path}"),
            Static(f"Signed IPA: {app.signed_ipa_path}"),
            classes="app-detail"
        )
        
        # Action buttons
        with Horizontal(classes="detail-actions"):
            details.mount(
                Button("🔄 Refresh (Re-sign & Install)", id=f"refresh-{app_id}", variant="primary"),
                Button("📱 Reinstall", id=f"reinstall-{app_id}"),
                Button("🗑️ Uninstall from Device", id=f"uninstall-{app_id}", variant="warning"),
                Button("🗑️ Remove Record", id=f"remove-{app_id}", variant="error"),
            )
    
    @on(Button.Pressed)
    async def on_app_action(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if not button_id:
            return
        
        if button_id.startswith("refresh-"):
            app_id = button_id.replace("refresh-", "")
            await self.refresh_app(app_id)
        elif button_id.startswith("reinstall-"):
            app_id = button_id.replace("reinstall-", "")
            await self.reinstall_app(app_id)
        elif button_id.startswith("uninstall-"):
            app_id = button_id.replace("uninstall-", "")
            await self.uninstall_app(app_id)
        elif button_id.startswith("remove-"):
            app_id = button_id.replace("remove-", "")
            await self.remove_app(app_id)
    
    async def install_ipa(self) -> None:
        """Install a new IPA."""
        # First, select device
        devices = list(app_state.device_manager.devices.values())
        if not devices:
            self.notify("No devices paired. Pair a device first.", severity="error")
            return
        
        # Select signing config
        sign_configs = list(app_state.signing_manager.configs.values())
        if not sign_configs:
            self.notify("No signing configs. Create one in Signing tab.", severity="error")
            return
        
        class InstallScreen(ModalScreen):
            def __init__(self, devices, sign_configs):
                super().__init__()
                self.devices = devices
                self.sign_configs = sign_configs
            
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Install IPA", classes="dialog-title")
                    
                    yield Label("IPA File Path:")
                    yield Input(placeholder="/path/to/app.ipa", id="ipa-path")
                    
                    yield Label("Device:")
                    yield Select(
                        [(d.name, d.udid) for d in self.devices],
                        id="device-select"
                    )
                    
                    yield Label("Signing Config:")
                    yield Select(
                        [(c.name, c.id) for c in self.sign_configs],
                        id="sign-config-select"
                    )
                    
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Install", id="confirm", variant="primary")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#confirm")
            def confirm(self) -> None:
                self.dismiss({
                    "ipa_path": self.query_one("#ipa-path", Input).value,
                    "device_udid": self.query_one("#device-select", Select).value,
                    "sign_config_id": self.query_one("#sign-config-select", Select).value,
                })
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(None)
        
        result = await self.app.push_screen_wait(InstallScreen(devices, sign_configs))
        if not result or not result.get("ipa_path"):
            return
        
        ipa_path = result["ipa_path"]
        if not Path(ipa_path).exists():
            self.notify(f"IPA not found: {ipa_path}", severity="error")
            return
        
        self.notify("Signing and installing...")
        
        try:
            # Get bundle ID
            bundle_id = await app_state.signing_manager.get_bundle_id(ipa_path)
            if not bundle_id:
                self.notify("Could not extract bundle ID from IPA", severity="error")
                return
            
            # Sign IPA
            sign_config = app_state.signing_manager.configs[result["sign_config_id"]]
            device_udid = result["device_udid"]
            
            signed_ipa = await app_state.signing_manager.sign_ipa(
                ipa_path, sign_config, bundle_id, device_udid
            )
            
            # Install
            success = await app_state.device_manager.install_app(
                device_udid, signed_ipa, bundle_id
            )
            
            if not success:
                self.notify("Installation failed", severity="error")
                return
            
            # Save app record
            device = app_state.device_manager.devices[device_udid]
            app_info = AppInfo(
                id=str(uuid.uuid4()),
                name=Path(ipa_path).name,
                bundle_id=bundle_id,
                device_udid=device_udid,
                sign_config_id=result["sign_config_id"],
                original_ipa_path=ipa_path,
                signed_ipa_path=signed_ipa,
                installed_at=datetime.now(),
                expires_at=datetime.now() + timedelta(days=7),
                last_installed=datetime.now(),
            )
            
            app_state.device_manager.add_app(app_info)
            await app_state.storage.save_apps(list(app_state.device_manager.apps.values()))
            
            self.refresh_apps()
            self.notify(f"✅ {app_info.name} installed successfully!")
            
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
    
    async def refresh_app(self, app_id: str) -> None:
        """Re-sign and reinstall app."""
        self.notify("Refreshing app...")
        app = app_state.device_manager.apps.get(app_id)
        if not app:
            return
        
        try:
            sign_config = app_state.signing_manager.configs[app.sign_config_id]
            device = app_state.device_manager.devices[app.device_udid]
            
            # Re-sign
            signed_ipa = await app_state.signing_manager.sign_ipa(
                app.original_ipa_path, sign_config, app.bundle_id, device.udid
            )
            
            # Install
            success = await app_state.device_manager.install_app(
                device.udid, signed_ipa, app.bundle_id
            )
            
            if success:
                app.last_installed = datetime.now()
                app.expires_at = datetime.now() + timedelta(days=7)
                app.signed_ipa_path = signed_ipa
                await app_state.storage.save_apps(list(app_state.device_manager.apps.values()))
                self.refresh_apps()
                self.show_app_details(app_id)
                self.notify("App refreshed successfully!")
            else:
                self.notify("Refresh failed", severity="error")
                
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")
    
    async def reinstall_app(self, app_id: str) -> None:
        """Reinstall without re-signing."""
        self.notify("Reinstalling...")
        app = app_state.device_manager.apps.get(app_id)
        if not app:
            return
        
        device = app_state.device_manager.devices.get(app.device_udid)
        if not device:
            self.notify("Device not found", severity="error")
            return
        
        success = await app_state.device_manager.install_app(
            device.udid, app.signed_ipa_path, app.bundle_id
        )
        
        if success:
            app.last_installed = datetime.now()
            await app_state.storage.save_apps(list(app_state.device_manager.apps.values()))
            self.refresh_apps()
            self.notify("Reinstalled!")
        else:
            self.notify("Reinstall failed", severity="error")
    
    async def uninstall_app(self, app_id: str) -> None:
        app = app_state.device_manager.apps.get(app_id)
        if not app:
            return
        
        device = app_state.device_manager.devices.get(app.device_udid)
        if not device:
            self.notify("Device not found", severity="error")
            return
        
        success = await app_state.device_manager.uninstall_app(device.udid, app.bundle_id)
        if success:
            self.notify("Uninstalled from device")
        else:
            self.notify("Uninstall failed", severity="error")
    
    async def remove_app(self, app_id: str) -> None:
        class ConfirmScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Remove App Record?", classes="dialog-title")
                    yield Static("This only removes the local record, not the app from device.")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Remove", id="confirm", variant="error")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#confirm")
            def confirm(self) -> None:
                self.dismiss(True)
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(False)
        
        confirmed = await self.app.push_screen_wait(ConfirmScreen())
        if confirmed:
            app = app_state.device_manager.apps.get(app_id)
            if app:
                Path(app.original_ipa_path).unlink(missing_ok=True)
                Path(app.signed_ipa_path).unlink(missing_ok=True)
            
            app_state.device_manager.remove_app(app_id)
            await app_state.storage.save_apps(list(app_state.device_manager.apps.values()))
            self.refresh_apps()
            self.query_one("#app-details", Container).remove_children()
            self.query_one("#app-details", Container).mount(
                Static("Select an app to see details", classes="placeholder")
            )
            self.notify("App record removed")


class SigningContent(Static):
    """Signing configuration screen."""
    
    def compose(self) -> ComposeResult:
        with Container(classes="signing-screen"):
            with Horizontal(classes="toolbar"):
                yield Static("🔐 Signing Configurations", classes="section-title")
                yield Button("➕ Add Config", id="btn-add-config", variant="primary")
                yield Button("🔄 Refresh", id="btn-refresh-signing")
            yield DataTable(id="signing-table", cursor_type="row")
            with Container(id="signing-details", classes="details-panel"):
                yield Static("Select a config to see details", classes="placeholder")
    
    def on_mount(self) -> None:
        self.setup_table()
        self.refresh_configs()
    
    def setup_table(self) -> None:
        table = self.query_one("#signing-table", DataTable)
        table.add_columns("Name", "Type", "Team ID", "Bundle Prefix", "Valid", "Last Tested")
        table.zebra_stripes = True
    
    def refresh_configs(self) -> None:
        table = self.query_one("#signing-table", DataTable)
        table.clear()
        
        for config in app_state.signing_manager.configs.values():
            config_type = "Apple ID" if config.use_apple_id else "Certificate"
            valid = "✅" if config.is_valid else "❌"
            last_tested = config.last_tested.strftime("%m/%d %H:%M") if config.last_tested else "Never"
            
            table.add_row(
                config.name,
                config_type,
                config.team_id or "N/A",
                config.bundle_id_prefix or "N/A",
                valid,
                last_tested,
                key=config.id
            )
    
    @on(DataTable.RowSelected)
    def on_config_selected(self, event: DataTable.RowSelected) -> None:
        config_id = event.row_key.value
        app_state.selected_sign_config_id = config_id
        self.show_config_details(config_id)
    
    @on(Button.Pressed, "#btn-add-config")
    async def on_add_config(self) -> None:
        await self.add_config()
    
    @on(Button.Pressed, "#btn-refresh-signing")
    def on_refresh(self) -> None:
        self.refresh_configs()
    
    def show_config_details(self, config_id: str) -> None:
        config = app_state.signing_manager.configs.get(config_id)
        if not config:
            return
        
        details = self.query_one("#signing-details", Container)
        details.remove_children()
        
        config_type = "Apple ID" if config.use_apple_id else "Certificate + Profile"
        
        details.mount(
            Static(f"Config: {config.name}", classes="detail-title"),
            Static(f"Type: {config_type}"),
            Static(f"Team ID: {config.team_id or 'N/A'}"),
            Static(f"Bundle Prefix: {config.bundle_id_prefix or 'N/A'}"),
            Static(f"Valid: {'Yes' if config.is_valid else 'No'}"),
            Static(f"Last Tested: {config.last_tested.strftime('%Y-%m-%d %H:%M') if config.last_tested else 'Never'}"),
            classes="config-detail"
        )
        
        if config.use_apple_id:
            details.mount(
                Static(f"Apple ID: {config.apple_id}"),
                Static(f"Anisette Server: {config.anisette_server or 'Default'}"),
            )
        else:
            details.mount(
                Static(f"Certificate: {config.cert_path or 'Not set'}"),
                Static(f"Profile: {config.profile_path or 'Not set'}"),
            )
        
        with Horizontal(classes="detail-actions"):
            details.mount(
                Button("🧪 Test Config", id=f"test-{config_id}", variant="primary"),
                Button("✏️ Edit", id=f"edit-{config_id}"),
                Button("🗑️ Delete", id=f"delete-{config_id}", variant="error"),
            )
    
    @on(Button.Pressed)
    async def on_config_action(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if not button_id:
            return
        
        if button_id.startswith("test-"):
            config_id = button_id.replace("test-", "")
            await self.test_config(config_id)
        elif button_id.startswith("edit-"):
            config_id = button_id.replace("edit-", "")
            await self.edit_config(config_id)
        elif button_id.startswith("delete-"):
            config_id = button_id.replace("delete-", "")
            await self.delete_config(config_id)
    
    async def add_config(self) -> None:
        class ConfigTypeScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Add Signing Config", classes="dialog-title")
                    yield Static("Select type:")
                    yield Button("📱 Apple ID (auto-provisioning)", id="type-appleid", variant="primary")
                    yield Button("📜 Certificate + Provisioning Profile", id="type-cert", variant="primary")
                    yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#type-appleid")
            def apple_id(self) -> None:
                self.dismiss("appleid")
            
            @on(Button.Pressed, "#type-cert")
            def cert(self) -> None:
                self.dismiss("cert")
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(None)
        
        config_type = await self.app.push_screen_wait(ConfigTypeScreen())
        if not config_type:
            return
        
        if config_type == "appleid":
            await self.add_apple_id_config()
        else:
            await self.add_cert_config()
    
    async def add_apple_id_config(self) -> None:
        class AppleIdConfigScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Apple ID Signing Config", classes="dialog-title")
                    yield Label("Name:")
                    yield Input(placeholder="My Apple ID Config", id="name")
                    yield Label("Apple ID:")
                    yield Input(placeholder="user@example.com", id="apple-id")
                    yield Label("Password (App-Specific):")
                    yield Input(placeholder="xxxx-xxxx-xxxx-xxxx", id="password", password=True)
                    yield Label("Anisette Server (optional):")
                    yield Input(placeholder="https://anisette.example.com", id="anisette")
                    yield Label("Team ID (optional):")
                    yield Input(placeholder="ABCDE12345", id="team-id")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Save", id="save", variant="primary")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#save")
            def save(self) -> None:
                self.dismiss({
                    "name": self.query_one("#name", Input).value,
                    "apple_id": self.query_one("#apple-id", Input).value,
                    "password": self.query_one("#password", Input).value,
                    "anisette": self.query_one("#anisette", Input).value,
                    "team_id": self.query_one("#team-id", Input).value,
                })
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(None)
        
        result = await self.app.push_screen_wait(AppleIdConfigScreen())
        if not result or not result.get("apple_id"):
            return
        
        config = SignConfig(
            id=str(uuid.uuid4()),
            name=result["name"] or f"Apple ID: {result['apple_id']}",
            use_apple_id=True,
            apple_id=result["apple_id"],
            apple_id_password=result["password"],
            anisette_server=result["anisette"],
            team_id=result["team_id"],
        )
        
        app_state.signing_manager.add_config(config)
        await app_state.storage.save_sign_configs(list(app_state.signing_manager.configs.values()))
        self.refresh_configs()
        self.notify("Apple ID config added")
    
    async def add_cert_config(self) -> None:
        class CertConfigScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Certificate + Profile Config", classes="dialog-title")
                    yield Label("Name:")
                    yield Input(placeholder="My Cert Config", id="name")
                    yield Label("Certificate (.p12) Path:")
                    yield Input(placeholder="/path/to/cert.p12", id="cert-path")
                    yield Label("Certificate Password:")
                    yield Input(placeholder="password", id="cert-password", password=True)
                    yield Label("Provisioning Profile (.mobileprovision) Path:")
                    yield Input(placeholder="/path/to/profile.mobileprovision", id="profile-path")
                    yield Label("Team ID:")
                    yield Input(placeholder="ABCDE12345", id="team-id")
                    yield Label("Bundle ID Prefix (for wildcard):")
                    yield Input(placeholder="com.example.*", id="bundle-prefix")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Save", id="save", variant="primary")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#save")
            def save(self) -> None:
                self.dismiss({
                    "name": self.query_one("#name", Input).value,
                    "cert_path": self.query_one("#cert-path", Input).value,
                    "cert_password": self.query_one("#cert-password", Input).value,
                    "profile_path": self.query_one("#profile-path", Input).value,
                    "team_id": self.query_one("#team-id", Input).value,
                    "bundle_prefix": self.query_one("#bundle-prefix", Input).value,
                })
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(None)
        
        result = await self.app.push_screen_wait(CertConfigScreen())
        if not result:
            return
        
        config = SignConfig(
            id=str(uuid.uuid4()),
            name=result["name"] or "Certificate Config",
            cert_path=result["cert_path"],
            cert_password=result["cert_password"],
            profile_path=result["profile_path"],
            team_id=result["team_id"],
            bundle_id_prefix=result["bundle_prefix"],
            use_apple_id=False,
        )
        
        app_state.signing_manager.add_config(config)
        await app_state.storage.save_sign_configs(list(app_state.signing_manager.configs.values()))
        self.refresh_configs()
        self.notify("Certificate config added")
    
    async def test_config(self, config_id: str) -> None:
        config = app_state.signing_manager.configs.get(config_id)
        if not config:
            return
        
        self.notify("Testing config...")
        result = await app_state.signing_manager.test_config(config)
        
        if result["valid"]:
            self.notify("✅ Config is valid!")
        else:
            errors = "\n".join(result["errors"])
            self.notify(f"❌ Config invalid:\n{errors}", severity="error")
        
        self.refresh_configs()
        self.show_config_details(config_id)
    
    async def edit_config(self, config_id: str) -> None:
        # Similar to add but pre-filled
        self.notify("Edit not implemented yet", severity="warning")
    
    async def delete_config(self, config_id: str) -> None:
        class ConfirmScreen(ModalScreen):
            def compose(self) -> ComposeResult:
                with Container(classes="dialog"):
                    yield Static("Delete Config?", classes="dialog-title")
                    yield Static("This cannot be undone.")
                    with Horizontal(classes="dialog-buttons"):
                        yield Button("Delete", id="confirm", variant="error")
                        yield Button("Cancel", id="cancel")
            
            @on(Button.Pressed, "#confirm")
            def confirm(self) -> None:
                self.dismiss(True)
            
            @on(Button.Pressed, "#cancel")
            def cancel(self) -> None:
                self.dismiss(False)
        
        confirmed = await self.app.push_screen_wait(ConfirmScreen())
        if confirmed:
            app_state.signing_manager.remove_config(config_id)
            await app_state.storage.save_sign_configs(list(app_state.signing_manager.configs.values()))
            self.refresh_configs()
            self.query_one("#signing-details", Container).remove_children()
            self.query_one("#signing-details", Container).mount(
                Static("Select a config to see details", classes="placeholder")
            )
            self.notify("Config deleted")


class ConfigContent(Static):
    """Server configuration screen."""
    
    def compose(self) -> ComposeResult:
        with Container(classes="config-screen"):
            yield Static("⚙️ Server Configuration", classes="section-title")
            
            with Container(classes="config-form"):
                yield Label("Host:")
                yield Input(value=app_state.config.host, id="config-host")
                
                yield Label("Port:")
                yield Input(value=str(app_state.config.port), id="config-port")
                
                yield Label("Data Directory:")
                yield Input(value=app_state.config.data_dir, id="config-data-dir")
                
                yield Label("Upload Directory:")
                yield Input(value=app_state.config.upload_dir, id="config-upload-dir")
                
                yield Label("zsign Path:")
                yield Input(value=app_state.config.zsign_path, id="config-zsign")
                
                yield Label("mDNS Name:")
                yield Input(value=app_state.config.mDNS_name, id="config-mdns")
                
                yield Label("Auto Refresh:")
                yield Select([("Enabled", "true"), ("Disabled", "false")], 
                           value="true" if app_state.config.auto_refresh_enabled else "false",
                           id="config-auto-refresh")
                
                yield Label("Refresh Interval (hours):")
                yield Input(value=str(app_state.config.refresh_check_interval_hours), id="config-refresh-interval")
                
                with Horizontal(classes="dialog-buttons"):
                    yield Button("Save", id="save-config", variant="primary")
                    yield Button("Reset to Defaults", id="reset-config", variant="warning")
            
            with Container(classes="config-info"):
                yield Static("Storage Info", classes="section-title")
                yield Static("", id="storage-info")
    
    def on_mount(self) -> None:
        self.update_storage_info()
    
    def update_storage_info(self) -> None:
        data_dir = Path(app_state.config.data_dir)
        size = sum(f.stat().st_size for f in data_dir.rglob("*") if f.is_file()) if data_dir.exists() else 0
        size_mb = size / (1024 * 1024)
        
        info = self.query_one("#storage-info", Static)
        info.update(f"Data Directory: {data_dir.absolute()}\nSize: {size_mb:.2f} MB\nDevices: {len(app_state.device_manager.devices)}\nApps: {len(app_state.device_manager.apps)}\nSign Configs: {len(app_state.signing_manager.configs)}")
    
    @on(Button.Pressed, "#save-config")
    def save_config(self) -> None:
        app_state.config.host = self.query_one("#config-host", Input).value
        app_state.config.port = int(self.query_one("#config-port", Input).value or "8080")
        app_state.config.data_dir = self.query_one("#config-data-dir", Input).value
        app_state.config.upload_dir = self.query_one("#config-upload-dir", Input).value
        app_state.config.zsign_path = self.query_one("#config-zsign", Input).value
        app_state.config.mDNS_name = self.query_one("#config-mdns", Input).value
        app_state.config.auto_refresh_enabled = self.query_one("#config-auto-refresh", Select).value == "true"
        app_state.config.refresh_check_interval_hours = int(self.query_one("#config-refresh-interval", Input).value or "1")
        
        asyncio.create_task(app_state.storage.save_config(app_state.config))
        self.update_storage_info()
        self.notify("Configuration saved")
    
    @on(Button.Pressed, "#reset-config")
    def reset_config(self) -> None:
        app_state.config = ServerConfig()
        self.query_one("#config-host", Input).value = app_state.config.host
        self.query_one("#config-port", Input).value = str(app_state.config.port)
        self.query_one("#config-data-dir", Input).value = app_state.config.data_dir
        self.query_one("#config-upload-dir", Input).value = app_state.config.upload_dir
        self.query_one("#config-zsign", Input).value = app_state.config.zsign_path
        self.query_one("#config-mdns", Input).value = app_state.config.mDNS_name
        self.query_one("#config-auto-refresh", Select).value = "true" if app_state.config.auto_refresh_enabled else "false"
        self.query_one("#config-refresh-interval", Input).value = str(app_state.config.refresh_check_interval_hours)
        self.notify("Configuration reset to defaults")


# ===========================
# Main Application
# ===========================
class SideloadApp(App):
    """Main TUI Application."""
    
    TITLE = "iOS Sideload Server"
    SUB_TITLE = "Termux Edition (armv7a)"
    CSS_PATH = "tui.css"
    
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("ctrl+c", "quit", "Quit"),
        Binding("d", "switch_tab('dashboard')", "Dashboard"),
        Binding("v", "switch_tab('devices')", "Devices"),
        Binding("a", "switch_tab('apps')", "Apps"),
        Binding("s", "switch_tab('signing')", "Signing"),
        Binding("c", "switch_tab('config')", "Config"),
        Binding("r", "refresh_current", "Refresh"),
    ]
    
    def __init__(self, data_dir: str = "./data", upload_dir: str = "./uploads", zsign_path: str = "zsign"):
        super().__init__()
        self.data_dir = data_dir
        self.upload_dir = upload_dir
        self.zsign_path = zsign_path
    
    async def on_mount(self) -> None:
        # Initialize storage and managers
        app_state.storage = StorageManager(self.data_dir)
        await app_state.storage.initialize()
        
        app_state.device_manager = DeviceManager(app_state.storage, self.zsign_path)
        app_state.signing_manager = SigningManager(app_state.storage, self.zsign_path)
        
        # Load saved data
        config = await app_state.storage.load_config()
        if config:
            app_state.config = config
        
        devices = await app_state.storage.load_devices()
        for device in devices:
            app_state.device_manager.add_device(device)
        
        sign_configs = await app_state.storage.load_sign_configs()
        for sc in sign_configs:
            app_state.signing_manager.add_config(sc)
        
        apps = await app_state.storage.load_apps()
        for app_info in apps:
            app_state.device_manager.add_app(app_info)
        
        # Push main screen
        await self.push_screen(DashboardScreen())
        
        self.notify(f"iOS Sideload Server started\nData: {self.data_dir}")
    
    async def on_unmount(self) -> None:
        # Save all data on exit
        await app_state.storage.save_all(
            config=app_state.config,
            devices=list(app_state.device_manager.devices.values()),
            sign_configs=list(app_state.signing_manager.configs.values()),
            apps=list(app_state.device_manager.apps.values())
        )
    
    def action_switch_tab(self, tab: str) -> None:
        """Switch to a specific tab."""
        if hasattr(self, 'screen') and hasattr(self.screen, 'query_one'):
            try:
                tabs = self.screen.query_one("#main-tabs", TabbedContent)
                tabs.active = tab
                app_state.current_tab = tab
            except Exception:
                pass
    
    def action_refresh_current(self) -> None:
        """Refresh current tab."""
        # Trigger refresh on current screen content
        pass


# ===========================
# Entry Point
# ===========================
def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="iOS Sideload Server for Termux (TUI)")
    parser.add_argument("--data-dir", default="./data", help="Data directory")
    parser.add_argument("--upload-dir", default="./uploads", help="Upload directory")
    parser.add_argument("--zsign-path", default="zsign", help="Path to zsign binary")
    args = parser.parse_args()
    
    # Ensure directories exist
    Path(args.data_dir).mkdir(parents=True, exist_ok=True)
    Path(args.upload_dir).mkdir(parents=True, exist_ok=True)
    
    app = SideloadApp(
        data_dir=args.data_dir,
        upload_dir=args.upload_dir,
        zsign_path=args.zsign_path
    )
    app.run()


if __name__ == "__main__":
    main()