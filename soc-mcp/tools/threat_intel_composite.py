"""Composite threat-intel lookup tools for SOC MCP server.

Why this file exists: asking the agent model to remember to call four
separate per-source tools (VirusTotal, OTX, MalwareBazaar, ThreatFox) for
every single hash -- and to keep doing that reliably as a case grows to
many hashes -- proved unreliable with smaller/weaker models in practice
(observed skipping 3 of 4 sources once there were several hashes to
process). These composite tools fix that by making ONE tool call cover
ALL sources for one IOC, querying them in parallel server-side. The model
only has to remember "one call per IOC" instead of "four calls per IOC",
which is a much smaller reliability burden.

The four individual per-source tools (virustotal_lookup_hash,
otx_lookup_hash, malwarebazaar_lookup_hash, threatfox_lookup_ioc, and
their IP/domain siblings) remain available too, for cases where only one
specific source is actually wanted.

Each source result is normalized to one of three states, so a caller can
tell these apart (this distinction is the whole point -- conflating them
would silently treat "we don't know" as "it's clean"):
  - "malicious"  -- this source has it on record as malicious/known-bad.
  - "not_found"  -- this source was successfully queried and has no record
                     of it. Not evidence of safety, just silence from this
                     one source.
  - "error"      -- this source could NOT be queried (missing API key,
                     timeout, rate limit, etc.). We simply don't have an
                     answer from it.
"""

import asyncio
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from tools import abusech_read, otx_read, virustotal_read


def _headers() -> dict[str, str]:
    return get_http_headers()


def _error_result(detail: str) -> dict[str, Any]:
    return {"status": "error", "detail": detail}


def _detail_without_subject(result: dict[str, Any], subject: str, fallback: str) -> str:
    """Get a source's "summary" text with a redundant "{subject}: " prefix
    stripped, if present. Each underlying tool's own "summary" field
    prefixes the IOC value (useful when that tool is called standalone),
    but here it's noise -- the composite result already carries the IOC
    value once at the top level, and a caller rendering all four sources
    side by side doesn't need the same 64-character hash repeated in
    every single line.
    """
    text = result.get("summary", fallback)
    prefix = f"{subject}: "
    return text[len(prefix) :] if text.startswith(prefix) else text


async def _check_virustotal_hash(file_hash: str, api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return _error_result("VirusTotal API key not configured in Tracecat Credentials")
    result = await virustotal_read.lookup_hash(file_hash, api_key)
    if "error" in result:
        return _error_result(result["error"])
    votes = result.get("malicious_votes", 0)
    total = result.get("total_vendors", 0)
    if votes == 0:
        return {
            "status": "not_found",
            "detail": _detail_without_subject(result, file_hash, "not found"),
        }
    # A vote ratio this low is a weak, often false-positive-prone signal --
    # surface the raw numbers AND a pre-computed verdict on their strength,
    # so the caller doesn't have to do this arithmetic itself.
    strong_signal = votes >= 20 or (total > 0 and votes / total >= 0.25)
    return {
        "status": "malicious",
        "vendor_votes": votes,
        "vendor_total": total,
        "strong_signal": strong_signal,
        "detail": _detail_without_subject(result, file_hash, "flagged"),
    }


async def _check_otx_hash(file_hash: str, api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return _error_result("OTX API key not configured in Tracecat Credentials")
    result = await otx_read.lookup_hash(file_hash, api_key)
    if "error" in result:
        return _error_result(result["error"])
    count = result.get("pulse_count", 0)
    if count == 0:
        return {
            "status": "not_found",
            "detail": _detail_without_subject(result, file_hash, "not found"),
        }
    return {
        "status": "malicious",
        "pulse_count": count,
        # pulse_names/tags are the only source of threat_actor/campaign
        # attribution this composite tool has access to -- dropping them
        # (as an earlier version of this function did) silently breaks
        # attribution for every hash IOC, since hash IOCs are required to
        # use this composite tool instead of the individual otx_lookup_hash
        # tool that would otherwise expose this data.
        "pulse_names": result.get("pulse_names", []),
        "tags": result.get("tags", []),
        "detail": _detail_without_subject(result, file_hash, "flagged"),
    }


async def _check_malwarebazaar_hash(file_hash: str, api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return _error_result("abuse.ch API key not configured in Tracecat Credentials")
    result = await abusech_read.lookup_hash_malwarebazaar(file_hash, api_key)
    if "error" in result:
        return _error_result(result["error"])
    if result.get("known"):
        return {
            "status": "malicious",
            "malware_family": result.get("signature"),
            "detail": _detail_without_subject(result, file_hash, "flagged"),
        }
    return {
        "status": "not_found",
        "detail": _detail_without_subject(result, file_hash, "not found"),
    }


async def _check_threatfox_hash(file_hash: str, api_key: str | None) -> dict[str, Any]:
    if not api_key:
        return _error_result("abuse.ch API key not configured in Tracecat Credentials")
    result = await abusech_read.lookup_ioc_threatfox(file_hash, api_key)
    if "error" in result:
        return _error_result(result["error"])
    if result.get("known"):
        return {
            "status": "malicious",
            "malware_families": result.get("malware_families"),
            "threat_types": result.get("threat_types"),
            "detail": _detail_without_subject(result, file_hash, "flagged"),
        }
    return {
        "status": "not_found",
        "detail": _detail_without_subject(result, file_hash, "not found"),
    }


def _summarize(sources: dict[str, dict[str, Any]], subject: str) -> str:
    malicious = []
    for name, s in sources.items():
        if s["status"] != "malicious":
            continue
        if "vendor_votes" in s:
            malicious.append(f"{name} ({s['vendor_votes']}/{s['vendor_total']}, {'strong' if s.get('strong_signal') else 'weak'})")
        else:
            malicious.append(name)
    not_found = [name for name, s in sources.items() if s["status"] == "not_found"]
    errored = [name for name, s in sources.items() if s["status"] == "error"]
    parts = []
    if malicious:
        parts.append(f"Flagged malicious by {', '.join(malicious)}")
    if not_found:
        parts.append(f"not found in {', '.join(not_found)}")
    if errored:
        parts.append(f"could not check {', '.join(errored)}")
    return f"{subject}: " + "; ".join(parts) if parts else f"{subject}: no sources checked"


def register_threat_intel_composite_tools(mcp: FastMCP):

    @mcp.tool()
    async def hash_lookup_all_sources(file_hash: str) -> dict:
        """Check a file hash (MD5/SHA1/SHA256) against ALL threat-intel
        sources at once -- VirusTotal, AlienVault OTX, MalwareBazaar, and
        ThreatFox -- in a single call. Use this INSTEAD of calling the
        individual per-source hash tools: one call here guarantees full
        coverage for this one hash, which the individual tools cannot
        guarantee across many hashes in a busy investigation. Call this
        once per hash, no matter how many hashes you have to check.
        """
        headers = _headers()
        vt, otx, mb, tf = await asyncio.gather(
            _check_virustotal_hash(file_hash, headers.get("x-virustotal-key")),
            _check_otx_hash(file_hash, headers.get("x-otx-key")),
            _check_malwarebazaar_hash(file_hash, headers.get("x-abusech-key")),
            _check_threatfox_hash(file_hash, headers.get("x-abusech-key")),
        )
        sources = {
            "virustotal": vt,
            "otx": otx,
            "malwarebazaar": mb,
            "threatfox": tf,
        }
        return {
            "hash": file_hash,
            "sources": sources,
            "malicious": any(s["status"] == "malicious" for s in sources.values()),
            "sources_checked": sum(1 for s in sources.values() if s["status"] != "error"),
            "sources_total": len(sources),
            "summary": _summarize(sources, file_hash),
        }
