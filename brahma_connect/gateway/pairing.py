from __future__ import annotations

import secrets
import time
from ipaddress import IPv4Address, ip_address, ip_network
from typing import Any

from .authentication import generate_pairing_token
from .models import PairingOffer


_LOCAL_IPV4_NETWORKS = (
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("100.0.0.0/8"),
)
_LOCAL_IPV6_NETWORKS = (
    ip_network("fc00::/7"),
    ip_network("fe80::/10"),
)


def is_trusted_local_address(address: str | None) -> bool:
    """Return whether a WebSocket peer is on a trusted local address.

    The headless Termux deployment has no desktop approval dialog, so the
    gateway may bootstrap a companion from loopback or a local network.  Do
    not resolve hostnames here: the decision must be based on the peer address
    reported by the WebSocket server.

    ``100.*`` is included deliberately for the phone/carrier-local network
    used by some Termux setups. The other explicit ranges are RFC 1918
    private networks plus IPv6 unique-local/link-local networks. IPv4-mapped
    IPv6 loopback/private addresses are normalized before checking.
    """
    raw = str(address or "").strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    # IPv6 link-local addresses may include a zone identifier (for example
    # fe80::1%wlan0), which ip_address() does not accept on every Python
    # version.
    raw = raw.split("%", 1)[0]
    if not raw:
        return False

    try:
        parsed = ip_address(raw)
    except ValueError:
        return False

    mapped = getattr(parsed, "ipv4_mapped", None)
    if mapped is not None:
        parsed = mapped

    # Check loopback before is_reserved: Python classifies IPv6 ::1 as both
    # loopback and reserved, but loopback is explicitly trusted here.
    if parsed.is_loopback:
        return True
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_reserved:
        return False
    if isinstance(parsed, IPv4Address):
        return any(parsed in network for network in _LOCAL_IPV4_NETWORKS)
    return any(parsed in network for network in _LOCAL_IPV6_NETWORKS)


class PairingManager:
    def __init__(self, service_name: str = "_BRAHMA._tcp.local.", ttl_seconds: int = 300):
        self.service_name = service_name
        self.ttl_seconds = max(60, int(ttl_seconds))
        self._offers: dict[str, PairingOffer] = {}
        self._code_index: dict[str, str] = {}

    @staticmethod
    def is_trusted_local_address(address: str | None) -> bool:
        """Expose the local trust policy used by the gateway handshake."""
        return is_trusted_local_address(address)

    def _prune(self) -> None:
        now = time.time()
        stale = [token for token, offer in self._offers.items() if offer.expires_at <= now]
        for token in stale:
            offer = self._offers.pop(token, None)
            if offer is not None:
                self._code_index.pop(offer.pairing_code, None)

    def create_offer(self, host: str, port: int) -> PairingOffer:
        self._prune()
        token = generate_pairing_token()
        code = f"{secrets.randbelow(1_000_000):06d}"
        offer = PairingOffer(
            service=self.service_name,
            host=host,
            port=int(port),
            pairing_token=token,
            pairing_code=code,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._offers[token] = offer
        self._code_index[code] = token
        return offer

    def get_offer(self, pairing_token: str) -> PairingOffer | None:
        self._prune()
        return self._offers.get(pairing_token)

    def get_offer_by_code(self, pairing_code: str) -> PairingOffer | None:
        self._prune()
        token = self._code_index.get(str(pairing_code).strip())
        if not token:
            return None
        return self._offers.get(token)

    def approve(self, pairing_token: str) -> PairingOffer | None:
        self._prune()
        offer = self._offers.pop(pairing_token, None)
        if offer is None:
            return None
        self._code_index.pop(offer.pairing_code, None)
        return offer

    def reject(self, pairing_token: str) -> bool:
        self._prune()
        offer = self._offers.pop(pairing_token, None)
        if offer is None:
            return False
        self._code_index.pop(offer.pairing_code, None)
        return True

    def to_qr_payload(self, offer: PairingOffer) -> dict[str, Any]:
        return offer.to_dict()
