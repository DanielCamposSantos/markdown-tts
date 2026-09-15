from __future__ import annotations

from app.pronunciation.resolver import PronunciationEntry, PronunciationResolver


PT_BR_PROFILE = "pt-BR-v3"

# Semantic names remain independent from model-specific synthesis cues.
PT_BR_CANONICAL_LETTER_NAMES = {
    "A": "a", "B": "bê", "C": "cê", "D": "dê", "E": "e",
    "F": "efe", "G": "gê", "H": "agá", "I": "i", "J": "jota",
    "K": "cá", "L": "ele", "M": "eme", "N": "ene", "O": "o",
    "P": "pê", "Q": "quê", "R": "erre", "S": "esse", "T": "tê",
    "U": "u", "V": "vê", "W": "dáblio", "X": "xis",
    "Y": "ípsilon", "Z": "zê",
}

# Synthesis cues tuned for MOSS, not official spellings of letter names.
PT_BR_TTS_LETTER_CUES = {
    "A": "á", "B": "bê", "C": "cê", "D": "dê", "E": "é",
    "F": "éfi", "G": "gê", "H": "agá", "I": "i", "J": "jota",
    "K": "cá", "L": "éli", "M": "êmi", "N": "êni", "O": "ó",
    "P": "pê", "Q": "quê", "R": "érri", "S": "éssi", "T": "tê",
    "U": "u", "V": "vê", "W": "dáblio", "X": "xis",
    "Y": "ípsilom", "Z": "zê",
}

PT_BR_TTS_DIGIT_CUES = {"2": "dois", "3": "três"}
SILENT_COMPOUND_SEPARATORS = frozenset("-/")


def spell_for_tts(token: str) -> str:
    cues = []
    for character in token.upper():
        if character in PT_BR_TTS_LETTER_CUES:
            cues.append(PT_BR_TTS_LETTER_CUES[character])
        elif character in PT_BR_TTS_DIGIT_CUES:
            cues.append(PT_BR_TTS_DIGIT_CUES[character])
        elif character not in SILENT_COMPOUND_SEPARATORS:
            raise ValueError(f"Unsupported character in controlled acronym: {character}")
    if not cues:
        raise ValueError("Controlled acronym cannot have an empty synthesis cue")
    return " ".join(cues)


CONTROLLED_ACRONYMS = (
    "IP", "TCP", "UDP", "HTTP", "HTTPS", "TLS", "SSL", "DNS", "DHCP",
    "ARP", "ICMP", "MAC", "NIC", "LAN", "WAN", "WLAN", "VLAN", "NAT",
    "SSH", "FTP", "SFTP", "SMTP", "IMAP", "POP", "SNMP", "SMB", "RDP",
    "RPC", "LDAP", "NTP", "VPN", "PKI", "TCP/IP",
    "WEP", "WPA", "WPA2", "WPA3", "SSID", "BSSID",
    "SOC", "SIEM", "SOAR", "EDR", "XDR", "MDR", "NDR", "IDS", "IPS",
    "HIDS", "NIDS", "WAF", "IAM", "PAM", "MFA", "SSO", "RBAC", "ABAC",
    "ACL", "DLP", "CASB", "CSPM", "CNAPP", "UEBA",
    "CVE", "CVSS", "CWE", "IOC", "IOA", "TTP", "APT", "CTI", "OSINT",
    "RAT", "DOS", "DDOS", "MITM", "XSS", "CSRF", "SSRF", "RCE", "LFI",
    "RFI", "XXE", "IDOR", "SSTI", "SQL", "API", "URL", "URI", "XML",
    "HTML", "JWT", "AES", "RSA", "SHA", "HMAC", "OTP", "TOTP", "HOTP",
    "CA", "CRL", "OCSP", "CSR", "OS", "VM", "CPU", "GPU", "RAM", "CLI",
    "GUI", "USB", "BIOS", "UEFI", "SYN", "ACK", "SYN-ACK",
)


def _build_entries() -> tuple[PronunciationEntry, ...]:
    if len(CONTROLLED_ACRONYMS) != len(set(CONTROLLED_ACRONYMS)):
        raise ValueError("Controlled pronunciation tokens must be unique")
    spelling = tuple(
        PronunciationEntry(token.casefold(), token, "spell_letters", spell_for_tts(token))
        for token in CONTROLLED_ACRONYMS
    )
    return (PronunciationEntry("cplusplus", "C++", "replace", "cê mais mais"), *spelling)


PT_BR_ENTRIES = _build_entries()
PT_BR_RESOLVER = PronunciationResolver(PT_BR_PROFILE, PT_BR_ENTRIES)
