from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
from typing import Any


HOSTNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{0,62}$")


def _run_ps(script: str, timeout: int = 30) -> str:
    if os.name != "nt":
        raise RuntimeError("Windows network configuration is available only on Windows Server.")
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or "PowerShell command failed.").strip()[-6000:])
    return proc.stdout.strip()


def _ps_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def list_adapters() -> list[dict[str, Any]]:
    raw = _run_ps(
        "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,MacAddress,ifIndex | ConvertTo-Json -Compress"
    )
    if not raw:
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    return data


def current_network() -> dict[str, Any]:
    if os.name != "nt":
        return {}
    try:
        raw = _run_ps(
            "Get-NetIPConfiguration | Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer | ConvertTo-Json -Compress"
        )
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def validate_network_config(data: dict[str, Any]) -> dict[str, Any]:
    hostname = str(data.get("hostname") or "").strip()
    ip = str(data.get("ip_address") or "").strip()
    prefix = str(data.get("prefix_length") or "24").strip()
    gateway = str(data.get("gateway") or "").strip()
    dns = [x.strip() for x in str(data.get("dns_servers") or "").split(",") if x.strip()]
    ntp = [x.strip() for x in str(data.get("ntp_servers") or "").split(",") if x.strip()]
    adapter = str(data.get("adapter") or "").strip()

    if not hostname or not HOSTNAME_RE.fullmatch(hostname):
        raise ValueError("Hostname must contain only letters, numbers, hyphens and dots and be 1-63 characters.")
    try:
        ipaddress.ip_address(ip)
        if ":" in ip:
            raise ValueError("IPv6 is not supported by this configuration screen; enter an IPv4 address.")
    except ValueError as exc:
        raise ValueError(f"Invalid IPv4 address: {ip}") from exc
    try:
        prefix_i = int(prefix)
    except ValueError as exc:
        raise ValueError("Prefix length must be an integer between 1 and 32.") from exc
    if not 1 <= prefix_i <= 32:
        raise ValueError("Prefix length must be between 1 and 32.")
    if gateway:
        try:
            ipaddress.IPv4Address(gateway)
        except ValueError as exc:
            raise ValueError(f"Invalid gateway: {gateway}") from exc
    for value in dns + ntp:
        if ":" in value:
            continue
        try:
            ipaddress.ip_address(value)
        except ValueError as exc:
            # NTP can also be a hostname.
            if value not in ntp or not HOSTNAME_RE.fullmatch(value):
                raise ValueError(f"Invalid DNS/NTP value: {value}") from exc
    if not adapter:
        raise ValueError("Select a Windows network adapter.")
    return {
        "adapter": adapter,
        "hostname": hostname,
        "ip_address": ip,
        "prefix_length": prefix_i,
        "gateway": gateway,
        "dns_servers": dns,
        "ntp_servers": ntp,
    }


def apply_network_config(data: dict[str, Any]) -> dict[str, Any]:
    cfg = validate_network_config(data)
    # Verify the adapter exists before making any changes.
    adapters = list_adapters()
    names = {str(a.get("Name") or "") for a in adapters}
    if cfg["adapter"] not in names:
        raise ValueError(f"Network adapter '{cfg['adapter']}' was not found.")

    adapter = _ps_quote(cfg["adapter"])
    ip = _ps_quote(cfg["ip_address"])
    prefix = cfg["prefix_length"]
    gateway = _ps_quote(cfg["gateway"]) if cfg["gateway"] else "$null"
    hostname = _ps_quote(cfg["hostname"])

    gateway_cmd = (
        f"New-NetIPAddress -InterfaceAlias {adapter} -IPAddress {ip} -PrefixLength {prefix} -DefaultGateway {gateway} -AddressFamily IPv4 -ErrorAction Stop"
        if cfg["gateway"]
        else
        f"New-NetIPAddress -InterfaceAlias {adapter} -IPAddress {ip} -PrefixLength {prefix} -AddressFamily IPv4 -ErrorAction Stop"
    )
    dns_array = ", ".join(_ps_quote(x) for x in cfg["dns_servers"])
    dns_cmd = (
        f"Set-DnsClientServerAddress -InterfaceAlias {adapter} -ServerAddresses @({dns_array}) -ErrorAction Stop"
        if cfg["dns_servers"] else ""
    )
    ntp_list = " ".join(cfg["ntp_servers"])
    ntp_cmd = (
        f'w32tm /config /manualpeerlist:"{ntp_list}" /syncfromflags:manual /update | Out-Null; '
        "Restart-Service w32time -Force; w32tm /resync /force | Out-Null"
        if cfg["ntp_servers"] else ""
    )

    script = f"""
$ErrorActionPreference = 'Stop'
$adapter = {adapter}
Get-NetIPAddress -InterfaceAlias $adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.PrefixOrigin -eq 'Dhcp' -or $_.IPAddress -ne {ip} }} |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
{gateway_cmd}
{dns_cmd}
if ((hostname) -ne {hostname}) {{
    Rename-Computer -NewName {hostname} -Force -ErrorAction Stop
}}
{ntp_cmd}
Get-NetIPConfiguration -InterfaceAlias $adapter |
    Select-Object InterfaceAlias,IPv4Address,IPv4DefaultGateway,DNSServer |
    ConvertTo-Json -Compress
"""
    result = _run_ps(script, timeout=90)
    return {"config": cfg, "result": result, "restart_required": True}
