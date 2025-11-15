# src/meraki_requests_stage.py
import os
import json
import time
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv
from tenacity import retry, stop_after_attempt, wait_exponential

BASE = "https://api.meraki.com/api/v1"

def pretty(o): return json.dumps(o, indent=2, sort_keys=True)

def _clean_key(v: Optional[str]) -> str:
    return (v or "").strip().strip('"').strip("'")

def _mask(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 6:
        return "*" * len(k)
    return k[:3] + "*" * (len(k)-6) + k[-3:]

def make_headers(api_key: str) -> dict:
    k = _clean_key(api_key)
    return {
        # send BOTH, harmless and avoids middleboxes stripping one
        "Authorization": f"Bearer {k}",
        "X-Cisco-Meraki-API-Key": k,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ena-mac-requests/1.0",
    }

def meraki_session(headers: dict) -> requests.Session:
    s = requests.Session()
    s.trust_env = False              # ignore HTTP(S)_PROXY env vars
    s.headers.update(headers)
    s.proxies = {"http": None, "https": None}
    return s

def rate_limit_sleep(resp: requests.Response):
    if resp.status_code == 429:
        retry_after = int(resp.headers.get("Retry-After", "1"))
        time.sleep(retry_after)

@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=8))
def _get(s: requests.Session, url: str, **kwargs):
    r = s.get(url, timeout=20, **kwargs)
    if r.status_code == 401:
        raise SystemExit(
            "401 Unauthorized from Meraki API.\n"
            "Checklist:\n"
            " - Org > Settings > Enable Dashboard API access\n"
            " - API key user has org access\n"
            " - If API IP allow list is on, your public IP is whitelisted\n"
            " - If unsure, regenerate key and update .env\n"
            f"Response body: {r.text[:400]}"
        )
    if r.status_code == 429:
        rate_limit_sleep(r)
        raise Exception("429 rate limited")
    r.raise_for_status()
    return r.json()

@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=1, max=8))
def _put(s: requests.Session, url: str, payload: Dict[str, Any]):
    r = s.put(url, json=payload, timeout=20)
    if r.status_code == 429:
        rate_limit_sleep(r)
        raise Exception("429")
    r.raise_for_status()
    return r.json()

def find_org_id_by_name(s: requests.Session, name: str) -> Optional[str]:
    orgs = _get(s, f"{BASE}/organizations")
    for o in orgs:
        if o.get("name") == name:
            return o["id"]
    return None

def find_network_id(s: requests.Session, org_id: str, net_name: str) -> Optional[str]:
    nets = _get(s, f"{BASE}/organizations/{org_id}/networks")
    for n in nets:
        if n.get("name") == net_name:
            return n["id"]
    return None

CONFIG = {
    "organization_name": "Demo_Silvan",
    "network_name": "API Network",
    "device_serial": "Q4CB-5A2N-KS54",
    "target_ports": ["4", "6"],
    "staging_vlan": 99,
    "dry_run": True,
    "teams_notify": False
}

def sanity_list_orgs(s: requests.Session):
    data = _get(s, f"{BASE}/organizations")
    print(f"✅ Sanity: fetched {len(data)} orgs")
    print([o.get("name") for o in data][:5])

def main():
    # ---- Resolve project root and .env explicitly ----
    script_path = Path(__file__).resolve()
    project_root = script_path.parent.parent          # repo root: one level above src/
    dotenv_path = project_root / ".env"

    # Load only this .env (don’t scan the world)
    loaded = False
    if dotenv_path.exists():
        load_dotenv(dotenv_path, override=False)
        loaded = True

    # Prefer an already-exported env var over .env (more explicit)
    raw_env = os.environ.get("MERAKI_API_KEY_ENAUTO")
    api = _clean_key(raw_env)

    # Debug prints to show exactly what was used
    print(f"Working dir:      {Path.cwd()}")
    print(f"Script path:      {script_path}")
    print(f"Project root:     {project_root}")
    print(f".env expected at: {dotenv_path}  (exists={dotenv_path.exists()})")
    print(f".env loaded:      {loaded}")
    print(f"API key present?: {bool(api)}")
    print(f"API key (masked): {_mask(api)}")

    if not api or api.lower() in {"your_api_key_here", "placeholder"} or len(api) < 20:
        raise SystemExit(
            "MERAKI_API_KEY_ENAUTO is not a valid-looking key.\n"
            "Fix it by either:\n"
            " - export MERAKI_API_KEY_ENAUTO=real_key   (in your shell), or\n"
            f" - put MERAKI_API_KEY_ENAUTO=real_key into {dotenv_path}\n"
            "Then rerun the script."
        )

    teams = os.getenv("TEAMS_WEBHOOK_URL")
    print("Teams webhook present?:", bool(teams))

    headers = make_headers(api)
    session = meraki_session(headers)

    # Sanity: should now mirror your curl success
    sanity_list_orgs(session)
    print("✅ HTTP layer ready")

    org_id = find_org_id_by_name(session, CONFIG["organization_name"])
    assert org_id, "Organization not found"
    print("✅ Org ID:", org_id)

    net_id = find_network_id(session, org_id, CONFIG["network_name"])
    assert net_id, "Network not found"
    print("✅ Network ID:", net_id)

if __name__ == "__main__":
    main()
