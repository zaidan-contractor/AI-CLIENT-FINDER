import time
import logging
from typing import Dict, Any, Tuple, Optional
import dns.resolver

logger = logging.getLogger(__name__)

class DNSVerifierService:
    """
    Real DNS Infrastructure Verification Service.
    Queries live DNS records using dnspython for SPF (TXT), DMARC (_dmarc TXT), and DKIM (selector._domainkey TXT).
    Caches results and provides diagnostic failure reasons.
    """
    _cache: Dict[str, Dict[str, Any]] = {}
    _CACHE_TTL = 300  # 5 minutes

    @classmethod
    def _extract_domain(cls, email_or_domain: str) -> str:
        if "@" in email_or_domain:
            return email_or_domain.split("@")[-1].strip().lower()
        return email_or_domain.strip().lower()

    @classmethod
    def verify_spf(cls, domain: str) -> Tuple[bool, str, Optional[str]]:
        """Verifies SPF TXT record on domain."""
        if domain.endswith(".test") or domain.endswith(".local") or domain.endswith(".example"):
            return True, "SANDBOX_VERIFIED", "v=spf1 include:_spf.example.com ~all"

        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = 3.0
            answers = resolver.resolve(domain, "TXT")
            for rdata in answers:
                txt_str = "".join([part.decode("utf-8") if isinstance(part, bytes) else str(part) for part in rdata.strings])
                if txt_str.startswith("v=spf1"):
                    return True, "VERIFIED", txt_str
            return False, "FAILED_NO_SPF_RECORD", None
        except dns.resolver.NXDOMAIN:
            return False, "FAILED_NXDOMAIN", None
        except dns.resolver.NoAnswer:
            return False, "FAILED_NO_TXT_RECORDS", None
        except dns.resolver.Timeout:
            return False, "TIMEOUT_DNS_UNREACHABLE", None
        except Exception as e:
            return False, f"DNS_ERROR: {str(e)}", None

    @classmethod
    def verify_dmarc(cls, domain: str) -> Tuple[bool, str, Optional[str]]:
        """Verifies DMARC TXT record on _dmarc.<domain>."""
        if domain.endswith(".test") or domain.endswith(".local") or domain.endswith(".example"):
            return True, "SANDBOX_VERIFIED", "v=DMARC1; p=reject; rua=mailto:dmarc@example.com"

        dmarc_host = f"_dmarc.{domain}"
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = 3.0
            answers = resolver.resolve(dmarc_host, "TXT")
            for rdata in answers:
                txt_str = "".join([part.decode("utf-8") if isinstance(part, bytes) else str(part) for part in rdata.strings])
                if txt_str.startswith("v=DMARC1"):
                    return True, "VERIFIED", txt_str
            return False, "FAILED_NO_DMARC_RECORD", None
        except dns.resolver.NXDOMAIN:
            return False, "FAILED_NXDOMAIN", None
        except dns.resolver.NoAnswer:
            return False, "FAILED_NO_TXT_RECORDS", None
        except dns.resolver.Timeout:
            return False, "TIMEOUT_DNS_UNREACHABLE", None
        except Exception as e:
            return False, f"DNS_ERROR: {str(e)}", None

    @classmethod
    def verify_dkim(cls, domain: str, selector: str = "default") -> Tuple[bool, str, Optional[str]]:
        """Verifies DKIM TXT record on <selector>._domainkey.<domain>."""
        if domain.endswith(".test") or domain.endswith(".local") or domain.endswith(".example"):
            return True, "SANDBOX_VERIFIED", f"v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQD"

        dkim_host = f"{selector}._domainkey.{domain}"
        try:
            resolver = dns.resolver.Resolver()
            resolver.lifetime = 3.0
            answers = resolver.resolve(dkim_host, "TXT")
            for rdata in answers:
                txt_str = "".join([part.decode("utf-8") if isinstance(part, bytes) else str(part) for part in rdata.strings])
                if "v=DKIM1" in txt_str or "p=" in txt_str:
                    return True, "VERIFIED", txt_str
            return False, "FAILED_NO_DKIM_KEY", None
        except dns.resolver.NXDOMAIN:
            return False, "FAILED_NXDOMAIN", None
        except dns.resolver.NoAnswer:
            return False, "FAILED_NO_TXT_RECORDS", None
        except dns.resolver.Timeout:
            return False, "TIMEOUT_DNS_UNREACHABLE", None
        except Exception as e:
            return False, f"DNS_ERROR: {str(e)}", None

    @classmethod
    def verify_sender_infrastructure(cls, email: str, dkim_selector: str = "default") -> Dict[str, Any]:
        """
        Runs comprehensive live DNS audit for sender domain.
        Returns detailed record states, timestamps, and readiness booleans.
        """
        domain = cls._extract_domain(email)
        cache_key = f"{domain}:{dkim_selector}"
        now = time.time()

        if cache_key in cls._cache:
            entry = cls._cache[cache_key]
            if now - entry["cached_at"] < cls._CACHE_TTL:
                return entry["data"]

        spf_ok, spf_status, spf_raw = cls.verify_spf(domain)
        dmarc_ok, dmarc_status, dmarc_raw = cls.verify_dmarc(domain)
        dkim_ok, dkim_status, dkim_raw = cls.verify_dkim(domain, dkim_selector)

        all_dns_valid = spf_ok and dmarc_ok and dkim_ok

        result = {
            "domain": domain,
            "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "all_dns_valid": all_dns_valid,
            "spf": {"valid": spf_ok, "status": spf_status, "record": spf_raw},
            "dmarc": {"valid": dmarc_ok, "status": dmarc_status, "record": dmarc_raw},
            "dkim": {"valid": dkim_ok, "status": dkim_status, "selector": dkim_selector, "record": dkim_raw}
        }

        cls._cache[cache_key] = {"cached_at": now, "data": result}
        return result
