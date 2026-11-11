from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse
from typing import Sequence

from app.connectors.base import EnterpriseConnectorError, SSRFSecurityViolationError


# Prohibited IP networks (IPv4 and IPv6)
BLOCKED_IP_NETWORKS: Sequence[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    # IPv4 Loopback
    ipaddress.ip_network("127.0.0.0/8"),
    # IPv4 RFC 1918 Private
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    # IPv4 Link-Local / Cloud Metadata (AWS, GCP, Azure, OpenStack)
    ipaddress.ip_network("169.254.0.0/16"),
    # IPv4 Carrier-Grade NAT
    ipaddress.ip_network("100.64.0.0/10"),
    # IPv4 Current Network / Broadcast / Reserved / Multicast
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("255.255.255.255/32"),
    # Documentation ranges
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # IPv6 Loopback & Unspecified
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::/128"),
    # IPv6 Unique Local (ULA)
    ipaddress.ip_network("fc00::/7"),
    # IPv6 Link-Local
    ipaddress.ip_network("fe80::/10"),
    # IPv6 Multicast
    ipaddress.ip_network("ff00::/8"),
    # IPv6 Documentation
    ipaddress.ip_network("2001:db8::/32"),
]


class SSRFProtectionGate:
    """Strict SSRF validation gate enforcing deny-by-default on private and internal IPs."""

    @classmethod
    def is_ip_blocked(cls, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        """Determines if a given IP address belongs to any blocked or private network."""
        # Standard library checks
        if (
            ip.is_loopback
            or ip.is_private
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return True

        # Explicit CIDR checks (covers CG-NAT 100.64.0.0/10 and cloud metadata 169.254.169.254)
        for net in BLOCKED_IP_NETWORKS:
            if ip in net:
                return True

        return False

    @classmethod
    def validate_target_url(
        cls,
        url: str,
        *,
        allow_private: bool | None = None,
        enforce_https: bool | None = None,
    ) -> str:
        """Validates that a URL does not point to internal, private, or cloud metadata resources.

        Args:
            url: The destination URL string to validate.
            allow_private: Explicit override (defaults to False, or env ALLOW_LOCAL_CONNECTORS).
            enforce_https: Whether HTTPS is strictly required.

        Returns:
            The normalized valid URL string.

        Raises:
            SSRFSecurityViolationError: If destination host resolves to private or metadata IP.
        """
        if not url or not isinstance(url, str):
            raise SSRFSecurityViolationError("URL cannot be empty")

        url_clean = url.strip()
        parsed = urllib.parse.urlsplit(url_clean)

        if parsed.scheme.lower() not in ("http", "https"):
            raise SSRFSecurityViolationError(
                f"Unsupported URL scheme '{parsed.scheme}'. Only HTTP and HTTPS are permitted."
            )

        if enforce_https and parsed.scheme.lower() != "https":
            raise SSRFSecurityViolationError("Insecure protocol: HTTPS is strictly required in production.")

        hostname = parsed.hostname
        if not hostname:
            raise SSRFSecurityViolationError("Target URL must have a valid non-empty hostname")

        # Explicit environment override check for testing
        is_private_allowed = (
            allow_private
            if allow_private is not None
            else os.getenv("ALLOW_LOCAL_CONNECTORS", "").lower() in ("true", "1", "yes")
        )

        if is_private_allowed:
            return url_clean

        # Check for literal localhost
        if hostname.lower() in ("localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback"):
            raise SSRFSecurityViolationError(
                f"SSRF violation: connection to '{hostname}' is forbidden (loopback target)."
            )

        # Check if hostname is already a direct IP literal (IPv4 or IPv6)
        try:
            direct_ip = ipaddress.ip_address(hostname)
            if cls.is_ip_blocked(direct_ip):
                raise SSRFSecurityViolationError(
                    f"SSRF violation: connection to IP '{direct_ip}' is blocked (private/reserved network)."
                )
            return url_clean
        except ValueError:
            # Hostname is a domain name, proceed with DNS resolution
            pass

        # Resolve DNS to all associated IP addresses
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        try:
            addr_info = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise SSRFSecurityViolationError(
                f"DNS resolution failed for host '{hostname}': {exc}"
            ) from exc

        if not addr_info:
            raise SSRFSecurityViolationError(f"No IP addresses resolved for host '{hostname}'")

        # Inspect all resolved addresses
        for item in addr_info:
            sockaddr = item[4]
            ip_str = sockaddr[0]
            try:
                resolved_ip = ipaddress.ip_address(ip_str)
                if cls.is_ip_blocked(resolved_ip):
                    raise SSRFSecurityViolationError(
                        f"SSRF violation: host '{hostname}' resolves to blocked IP '{resolved_ip}' (private/reserved network)."
                    )
            except ValueError:
                raise SSRFSecurityViolationError(
                    f"Invalid IP address returned from DNS resolution: '{ip_str}'"
                )

        return url_clean
