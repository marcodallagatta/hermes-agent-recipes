#!/usr/bin/env python3
"""Watch public free-model catalogs against configured AA baselines.

Report direct AA scores and explicitly labeled AA-backed proxies. The script
does not research outside benchmarks or run model inference.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SOURCES = {
    "Nous Portal": "https://inference-api.nousresearch.com/v1/models",
    "Vercel AI Gateway": "https://ai-gateway.vercel.sh/v1/models",
    "OpenRouter": "https://openrouter.ai/api/v1/models",
    "OpenCode Zen": "https://opencode.ai/zen/v1/models",
}
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_KEY_NAME = "ARTIFICIAL_ANALYSIS_API_KEY"
MAX_AA_PAGES = 25
SCHEMA = 3
AA_CACHE_TTL_SECONDS = 20 * 60 * 60
AA_CACHE_STATUS = ""
MAX_AA_INDEX_SCORE = Decimal("1000000")
MAX_CHART_ROWS = 50
EFFORT_LEVELS = frozenset(
    {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "reasoning", "thinking", "fast", "balanced"}
)


class WatchError(RuntimeError):
    """A config, source or response was unavailable or invalid."""


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def recipe_dir() -> Path:
    return hermes_home() / "cron" / "free-model-watch-recipe"


def config_path() -> Path:
    override = os.environ.get("FREE_MODEL_WATCH_RECIPE_CONFIG")
    return Path(override).expanduser() if override else recipe_dir() / "config.json"


def state_path() -> Path:
    return recipe_dir() / "state.json"


def aa_cache_path() -> Path:
    directory = hermes_home() / "cache" / "free-model-watch-recipe"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory / "aa_cache.json"


def load_aa_cache() -> dict | None:
    try:
        data = json.loads(aa_cache_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    version, records, fetched_at = data.get("version"), data.get("records"), data.get("fetched_at")
    if type(version) is not int or not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        return None
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)):
        return None
    timestamp = float(fetched_at)
    if not 0 < timestamp < float("inf"):
        return None
    return {"version": version, "records": records, "fetched_at": timestamp}


def save_aa_cache(version: int, records: list[dict], fetched_at: float | None = None) -> None:
    atomic_json(
        aa_cache_path(),
        {"version": version, "records": records, "fetched_at": time.time() if fetched_at is None else fetched_at},
    )


def aa_cache_note(fetched_at: float, *, stale: bool) -> str:
    stamp = datetime.fromtimestamp(fetched_at, timezone.utc).strftime("%d %b %Y %H:%M UTC")
    if stale:
        return f"Artificial Analysis fetch failed; using cached scores from {stamp}."
    return f"Artificial Analysis scores read from cache ({stamp})."


def safe_report_value(value: object, fallback: str = "", limit: int = 180) -> str:
    text = str(value if value is not None else fallback)
    text = "".join(char if char.isprintable() else " " for char in text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?i)MEDIA\s*:", "MEDIA：", text)
    return text[:limit] or fallback


def sanitize_report_text(output: str) -> str:
    text = "".join(char if char in "\n\t" or char.isprintable() else " " for char in str(output))
    return re.sub(r"(?i)MEDIA\s*:", "MEDIA：", text)


def append_aa_cache_status(output: str) -> str:
    if AA_CACHE_STATUS and output and output.strip() != '{"wakeAgent": false}':
        output = f"{output}\n\n{AA_CACHE_STATUS}"
    return sanitize_report_text(output)


def chart_path(now: datetime | None = None) -> Path:
    directory = hermes_home() / "cache" / "free-model-watch-recipe"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(directory, 0o700)
    return directory / "scores.png"


def load_config(path: Path | None = None) -> dict:
    path = path or config_path()
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WatchError(f"Cannot read watch configuration ({type(exc).__name__}).") from None
    if not isinstance(config, dict):
        raise WatchError("Watch configuration must be a JSON object.")
    zone = config.get("timezone")
    try:
        ZoneInfo(zone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise WatchError("Set timezone to a valid IANA time-zone name in config.json.") from None
    gate = config.get("gate")
    if not isinstance(gate, dict) or not str(gate.get("model", "")).strip() or not str(gate.get("effort", "")).strip():
        raise WatchError("Set gate.model and gate.effort to an Artificial Analysis model and tested effort.")
    refs = config.get("references")
    if not isinstance(refs, list) or not refs or any(
        not isinstance(x, dict) or not str(x.get("model", "")).strip() or not str(x.get("effort", "")).strip()
        for x in refs
    ):
        raise WatchError("Set at least one reference model and tested effort in references.")
    names = config.get("sources", list(SOURCES))
    if not isinstance(names, list) or not names or any(name not in SOURCES for name in names):
        raise WatchError("sources must list one or more supported catalog names.")
    config["sources"] = names
    promotions = config.get("promotions", [])
    if not isinstance(promotions, list):
        raise WatchError("promotions must be a list; use [] when no expiry reminders are configured.")
    for promotion in promotions:
        required = ("label", "source", "model_id", "provider", "model", "ends")
        if not isinstance(promotion, dict) or any(
            not isinstance(promotion.get(key), str) or not promotion[key].strip()
            for key in required
        ):
            raise WatchError("Each promotion needs string values for label, source, model_id, provider, model, and ends.")
        if promotion["source"] not in names:
            raise WatchError("Each promotion source must also appear in sources.")
        try:
            date.fromisoformat(promotion["ends"])
        except (TypeError, ValueError):
            raise WatchError("Promotion ends must be an ISO date (YYYY-MM-DD).") from None
        if type(promotion.get("end_date_estimated", False)) is not bool:
            raise WatchError("Promotion end_date_estimated must be true or false.")
        promotion.setdefault("end_date_estimated", False)
    config["promotions"] = promotions
    return config


def normalize(value: object) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\bfree\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def normalize_model_token(value: object) -> str:
    return str(value or "").strip().casefold()


def normalize_model_identity(value: object) -> str:
    text = str(value or "").strip().casefold()
    for suffix in (":free", "-free"):
        if text.endswith(suffix):
            return text[: -len(suffix)]
    return text


def is_free_model(source: str, record: dict) -> bool:
    if source not in SOURCES or not isinstance(record, dict):
        raise WatchError("Catalog entry has an unknown source or invalid shape.")
    model_id = record.get("id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise WatchError(f"{source} catalog entry has no string model ID.")
    if len(model_id) > 512 or "|" in model_id or any(not char.isprintable() for char in model_id):
        raise WatchError(f"{source} catalog entry has a malformed model ID.")
    if source == "Vercel AI Gateway":
        model_type = record.get("type")
        tags = record.get("tags", [])
        if tags is None:
            tags = []
        if not isinstance(model_type, str) or not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise WatchError("Vercel AI Gateway returned malformed type or tags fields.")
        return model_type == "language" and "free" in tags
    if source == "OpenCode Zen":
        return model_id.endswith("-free") or model_id.endswith(":free")
    return model_id.endswith(":free")


def model_key(model: dict) -> str:
    return f"{model['source']}|{model['id']}"


def aa_index_score(record: dict) -> Decimal | None:
    evaluations = record.get("evaluations") if isinstance(record, dict) else None
    if not isinstance(evaluations, dict):
        return None
    raw_score = evaluations.get("artificial_analysis_intelligence_index")
    if raw_score is None:
        return None
    try:
        score = Decimal(str(raw_score))
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None
    if not score.is_finite() or score < 0 or score > MAX_AA_INDEX_SCORE:
        return None
    digits = score.as_tuple()
    exponent = digits.exponent
    if not isinstance(exponent, int) or len(digits.digits) > 20 or not -30 <= exponent <= 30:
        return None
    try:
        numeric_score = float(score)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(numeric_score) or (score != 0 and numeric_score == 0):
        return None
    return score


def score_label(value) -> str:
    """Canonical numeric text so 75 and 75.0 compare equal in change detection."""
    number = Decimal(str(value))
    if not number.is_finite():
        raise WatchError("Score representation is not a finite number.")
    if number == 0:
        return "0"
    digits = number.as_tuple()
    exponent = digits.exponent
    if not isinstance(exponent, int) or len(digits.digits) > 20 or not -30 <= exponent <= 30:
        raise WatchError("Score representation is too large to format safely.")
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def aa_score_map(records: list[dict], models: list[dict]) -> dict[str, Decimal | None]:
    by_slug: dict[str, list[dict]] = {}
    for record in records:
        slug = normalize_model_token(record.get("slug"))
        if slug:
            by_slug.setdefault(slug, []).append(record)
    result: dict[str, Decimal | None] = {}
    for model in models:
        identity = normalize_model_token(model.get("id"))
        matches = by_slug.get(identity, []) if identity else []
        record = matches[0] if len(matches) == 1 else None
        result[model_key(model)] = aa_index_score(record) if record is not None else None
    return result


def effort_of(record: dict) -> str | None:
    match = re.search(r"\(([^()]*)\)\s*$", str(record.get("name") or ""))
    if not match:
        return None
    effort = match.group(1).strip()
    return effort if effort.casefold() in EFFORT_LEVELS else None


def aa_base_name(record: dict, fallback: str = "") -> str:
    name = str(record.get("name") or fallback).strip()
    if effort_of(record):
        name = re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()
    return name


def proxy_for(model: dict, records: list[dict], preferred_effort: str) -> dict | None:
    model_id = str(model.get("id") or "")
    identity = normalize_model_identity(model_id)
    if not identity:
        return None
    matches = []
    base_names = set()
    for record in records:
        if normalize_model_token(record.get("slug")) != identity:
            continue
        base_name = aa_base_name(record)
        if normalized_name := normalize_model_token(base_name):
            base_names.add(normalized_name)
        matches.append(record)
    if len(base_names) > 1:
        return None
    identities = [
        (str(record.get("id") or record.get("slug") or record.get("name")), (effort_of(record) or "").casefold())
        for record in matches
    ]
    if len(set(identities)) != len(identities):
        return None
    preferred = preferred_effort.casefold()
    exact_records = [record for record in matches if (effort_of(record) or "").casefold() == preferred]
    if exact_records:
        candidates = exact_records
    elif len(matches) == 1:
        candidates = matches
    else:
        return None
    if len(candidates) != 1:
        return None
    record = candidates[0]
    score = aa_index_score(record)
    if score is None:
        return None
    display_name = aa_base_name(record, model_id)
    return {"name": display_name or model_id, "score": score, "effort": effort_of(record)}


def proxy_snapshot(models: list[dict], records: list[dict], preferred_effort: str) -> dict[str, dict]:
    result = {}
    for model in models:
        proxy = proxy_for(model, records, preferred_effort)
        if proxy is None:
            continue
        result[model_key(model)] = {
            "source": model["source"],
            "id": model["id"],
            "name": model.get("name") or model["id"],
            "proxy_name": proxy["name"],
            "proxy_score": score_label(proxy["score"]),
            "proxy_effort": proxy.get("effort"),
        }
    return result


def normalize_hermes_provider(provider: object) -> str | None:
    if not isinstance(provider, str) or not provider.strip():
        return None
    try:
        from hermes_cli.providers import normalize_provider
        normalized = normalize_provider(provider.strip())
    except Exception:
        return None
    return normalized.strip().casefold() if isinstance(normalized, str) and normalized.strip() else None


def route_position(provider: str, model: str, configured_routes: list[tuple[str, str]] | None) -> str:
    if configured_routes is None:
        return "routing status unavailable"
    normalized_provider = normalize_hermes_provider(provider)
    if normalized_provider is None or not isinstance(model, str) or not model.strip():
        return "routing status unavailable"
    route = (normalized_provider, model.strip().casefold())
    try:
        index = configured_routes.index(route)
    except ValueError:
        if any(configured_model == model.strip().casefold() for _, configured_model in configured_routes):
            return "routing status unavailable"
        return "not configured in the current primary/fallback chain"
    return "configured as primary" if index == 0 else f"configured as fallback {index}"


def read_model_chain() -> list[tuple[str, str]] | None:
    """Read the effective primary/fallback chain, including the legacy fallback key."""
    hermes = shutil.which("hermes")
    if not hermes:
        return None
    env = os.environ.copy()
    env["HERMES_DISABLE_LAZY_INSTALLS"] = "1"
    unavailable = object()

    def get_config(key: str, *, optional: bool = False):
        result = subprocess.run(
            [hermes, "config", "get", key, "--json"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            env=env,
        )
        if result.returncode != 0:
            missing_key = f"config key not set: {key}"
            if optional and missing_key in (result.stderr + result.stdout).casefold():
                return []
            return unavailable
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            return unavailable

    try:
        model_config = get_config("model")
        fallback_config = get_config("fallback_providers", optional=True)
        legacy_config = get_config("fallback_model", optional=True)
    except (OSError, subprocess.SubprocessError):
        return None
    if model_config is unavailable or fallback_config is unavailable or legacy_config is unavailable:
        return None
    if not isinstance(model_config, dict):
        return None

    def as_entries(value):
        if value is None:
            return []
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return value
        return None

    current_entries = as_entries(fallback_config)
    legacy_entries = as_entries(legacy_config)
    if current_entries is None or legacy_entries is None:
        return None

    primary_provider_value = model_config.get("provider")
    primary_model_value = model_config.get("default")
    if not isinstance(primary_provider_value, str) or not isinstance(primary_model_value, str):
        return None
    primary_provider = normalize_hermes_provider(primary_provider_value)
    primary_model = primary_model_value.strip().casefold()
    if not primary_provider or not primary_model or primary_provider == "auto" or primary_model == "auto":
        return None

    primary_base_url = model_config.get("base_url")
    if primary_base_url is not None and not isinstance(primary_base_url, str):
        return None
    entries = [{
        "provider": primary_provider,
        "model": primary_model,
        "base_url": primary_base_url,
    }, *current_entries, *legacy_entries]
    routes = []
    base_urls = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None
        provider_value = entry.get("provider")
        model_value = entry.get("model")
        base_url_value = entry.get("base_url")
        if not isinstance(provider_value, str) or not isinstance(model_value, str):
            return None
        if base_url_value is not None and not isinstance(base_url_value, str):
            return None
        provider = normalize_hermes_provider(provider_value)
        model = model_value.strip().casefold()
        if not provider or not model or provider == "auto" or model == "auto":
            return None
        route = (provider, model)
        base_url = (base_url_value or "").strip().rstrip("/").casefold()
        if route in base_urls:
            if base_urls[route] != base_url:
                return None
            continue
        base_urls[route] = base_url
        routes.append(route)
    return routes


def promotion_alerts(
    promotions: list[dict],
    now: datetime,
    previous_notices: dict,
    configured_routes: list[tuple[str, str]] | None,
) -> tuple[list[dict], dict]:
    notices = dict(previous_notices or {})
    alerts = []
    for promotion in promotions:
        end = date.fromisoformat(promotion["ends"])
        days_left = (end - now.date()).days
        if days_left not in (0, 1):
            continue
        status = route_position(promotion["provider"], promotion["model"], configured_routes)
        if status == "not configured in the current primary/fallback chain":
            continue
        marker = (
            f"{promotion['source']}|{promotion['model_id']}|"
            f"{promotion['provider']}|{promotion['model']}|{promotion['ends']}|"
            f"{days_left}|{now.date().isoformat()}"
        )
        if marker in notices:
            continue
        timing = "expires today" if days_left == 0 else "1 day left"
        date_note = " (estimated end date)" if promotion.get("end_date_estimated") else ""
        lines = [
            "AGENT TASK: Promotion expiry",
            f"{safe_report_value(promotion['label'])} · {safe_report_value(promotion['source'])} · `{safe_report_value(promotion['model_id'])}`",
            f"Ends {end.day} {end.strftime('%b %Y')}{date_note}; {timing}.",
            f"Configured route status: {status}.",
        ]
        alerts.append({
            "text": "\n".join(lines),
            "source": promotion["source"],
            "model_id": promotion["model_id"],
        })
        notices[marker] = now.date().isoformat()
    return alerts, notices


def add_qualifying_alternatives(
    promotion_sections: list[dict],
    offers: list[dict],
    expiring_routes: set[tuple[str, str]] | None = None,
) -> list[str]:
    if not promotion_sections:
        return []
    expiring = set(expiring_routes or ())
    expiring.update((item["source"], item["model_id"]) for item in promotion_sections)
    result = []
    for promotion in promotion_sections:
        others = [
            offer for offer in offers
            if (offer["source"], offer["id"]) not in expiring
        ]
        if not others:
            suffix = "Current qualifying AA alternatives: none."
        else:
            lines = ["Current qualifying AA alternatives:"]
            lines.extend(
                f"- {safe_report_value(offer.get('name') or offer['id'])} · {safe_report_value(offer['source'])} · "
                f"`{safe_report_value(offer['id'])}` · AA Index {offer['score']:g}"
                for offer in sorted(others, key=lambda item: -item["score"])[:5]
            )
            suffix = "\n".join(lines)
        result.append(f"{promotion['text']}\n{suffix}")
    return result


def promotion_sections_with_unavailable_alternatives(promotion_sections: list[dict]) -> list[str]:
    return [
        f"{promotion['text']}\nCurrent qualifying AA alternatives: unavailable (watch coverage incomplete)."
        for promotion in promotion_sections
    ]


def classify_models(models: list[dict], scores: dict[str, Decimal | int | float | None], gate_score: Decimal | int | float):
    threshold = Decimal(str(gate_score))
    qualifying, unbenchmarked = [], []
    for model in models:
        score = scores.get(model_key(model))
        if score is None:
            unbenchmarked.append(model)
        elif Decimal(str(score)) >= threshold:
            qualifying.append({**model, "score": Decimal(str(score))})
    return qualifying, unbenchmarked


def snapshot_decision(previous: dict | None, current: dict, aa_version: int) -> dict:
    empty = {
        "added_offers": [], "removed_offers": [], "updated_offers": [],
        "added_proxies": [], "removed_proxies": [],
        "added_unbenchmarked": [], "removed_unbenchmarked": [],
    }
    if not previous or previous.get("schema") != SCHEMA or previous.get("aa_version") != aa_version:
        return {"wake_agent": False, "rebaseline": True, **empty}

    def offer_scores(values) -> dict[str, Decimal]:
        result = {}
        for value in values or []:
            if not isinstance(value, str):
                continue
            model_ref, separator, score = value.rpartition("|")
            if separator and model_ref and score:
                try:
                    result[model_ref] = Decimal(score)
                except (InvalidOperation, ValueError):
                    continue
        return result

    def norm_proxy(item):
        if not isinstance(item, dict):
            return item
        out = dict(item)
        if "proxy_score" in out:
            try:
                out["proxy_score"] = score_label(out["proxy_score"])
            except (InvalidOperation, ValueError, TypeError):
                pass
        return out

    old_offers = offer_scores(previous.get("offers"))
    new_offers = offer_scores(current.get("offers"))
    old_proxies = previous.get("proxies") if isinstance(previous.get("proxies"), dict) else {}
    new_proxies = current.get("proxies") if isinstance(current.get("proxies"), dict) else {}
    added = sorted(new_offers.keys() - old_offers.keys())
    removed = sorted(old_offers.keys() - new_offers.keys())
    updated = sorted(
        f"{key}|{score_label(new_offers[key])}"
        for key in old_offers.keys() & new_offers.keys()
        if old_offers[key] != new_offers[key]
    )
    proxy_additions = sorted(key for key, value in new_proxies.items() if norm_proxy(old_proxies.get(key)) != norm_proxy(value))
    proxy_removals = sorted(set(old_proxies) - set(new_proxies))
    return {
        "wake_agent": bool(added or removed or updated or proxy_additions or proxy_removals),
        "rebaseline": False,
        "added_offers": [f"{key}|{score_label(new_offers[key])}" for key in added],
        "removed_offers": [f"{key}|{score_label(old_offers[key])}" for key in removed],
        "updated_offers": updated,
        "added_proxies": proxy_additions,
        "removed_proxies": proxy_removals,
        "added_unbenchmarked": [],
        "removed_unbenchmarked": [],
    }


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".watch-", delete=False)
    try:
        json.dump(data, handle, sort_keys=True)
        handle.write("\n")
        handle.close()
        os.chmod(handle.name, 0o600)
        os.replace(handle.name, path)
    finally:
        if os.path.exists(handle.name):
            os.unlink(handle.name)


def read_key() -> str:
    value = os.environ.get(AA_KEY_NAME, "").strip()
    if value:
        return value
    env_file = hermes_home() / ".env"
    try:
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip().removeprefix("export ").strip()
            name, sep, value = line.partition("=")
            if sep and name.strip() == AA_KEY_NAME:
                return value.strip().strip("\"'")
    except OSError:
        pass
    raise WatchError(f"{AA_KEY_NAME} is not configured in the environment or Hermes .env.")


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward a credentialed request across an origin boundary."""

    @staticmethod
    def _origin(url: str) -> tuple[str, str, int] | None:
        try:
            parts = urlsplit(url)
            scheme = parts.scheme.casefold()
            if not parts.hostname or scheme not in {"http", "https"}:
                return None
            port = parts.port or (443 if scheme == "https" else 80)
            return scheme, parts.hostname.casefold(), port
        except ValueError:
            return None

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source, destination = self._origin(req.full_url), self._origin(newurl)
        if source is None or destination is None or source != destination:
            raise WatchError("Refusing cross-origin redirect for an authenticated JSON request.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    headers = headers or {}
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Hermes-Cron-Recipe/1.0", **headers})
    credentialed = any(key.casefold() in {"authorization", "proxy-authorization", "x-api-key"} for key in headers)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler()) if credentialed else None
    for attempt in range(3):
        try:
            response = opener.open(request, timeout=30) if opener else urllib.request.urlopen(request, timeout=30)
            with response:
                data = json.loads(response.read())
            if not isinstance(data, dict):
                raise WatchError(f"{url.split('/')[2]} returned an unexpected JSON object.")
            return data
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise WatchError(f"{url.split('/')[2]} returned HTTP {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise WatchError(f"Could not fetch valid JSON from {url.split('/')[2]}.") from None
    raise WatchError(f"{url.split('/')[2]} kept returning transient errors.")


def fetch_catalog(source: str) -> list[dict]:
    data = get_json(SOURCES[source]).get("data")
    if not isinstance(data, list):
        raise WatchError(f"{source} catalog is missing its data array.")
    if any(not isinstance(item, dict) for item in data):
        raise WatchError(f"{source} catalog contains malformed model entries.")
    return [item for item in data if is_free_model(source, item)]


def fetch_aa() -> tuple[int, list[dict]]:
    global AA_CACHE_STATUS
    AA_CACHE_STATUS = ""
    cache = load_aa_cache()
    now = time.time()
    if cache is not None:
        age = now - cache["fetched_at"]
        if age < 0:
            cache = None
        elif age < AA_CACHE_TTL_SECONDS:
            AA_CACHE_STATUS = aa_cache_note(cache["fetched_at"], stale=False)
            return cache["version"], cache["records"]

    key = read_key()
    records, version = [], None
    page_size_seen = total_pages_seen = None
    try:
        for page in range(1, MAX_AA_PAGES + 1):
            payload = get_json(f"{AA_MODELS_URL}?page={page}", {"x-api-key": key})
            page_version = payload.get("intelligence_index_version")
            if type(page_version) is not int:
                raise WatchError("Artificial Analysis omitted its index version.")
            if version is None:
                version = page_version
            elif page_version != version:
                raise WatchError("Artificial Analysis index version changed during pagination.")
            batch, pagination = payload.get("data"), payload.get("pagination")
            if not isinstance(batch, list) or not isinstance(pagination, dict):
                raise WatchError("Artificial Analysis returned an incomplete page.")
            declared_page = pagination.get("page")
            page_size = pagination.get("page_size")
            total_pages = pagination.get("total_pages")
            has_more = pagination.get("has_more")
            if type(declared_page) is not int or declared_page != page:
                raise WatchError("Artificial Analysis returned an unexpected pagination page number.")
            if type(page_size) is not int or page_size <= 0 or type(total_pages) is not int or total_pages <= 0:
                raise WatchError("Artificial Analysis returned invalid pagination sizes.")
            if total_pages > MAX_AA_PAGES:
                raise WatchError("Artificial Analysis pagination exceeded the configured page limit.")
            if not isinstance(has_more, bool) or has_more != (page < total_pages):
                raise WatchError("Artificial Analysis pagination metadata is inconsistent.")
            if page_size_seen is None:
                page_size_seen, total_pages_seen = page_size, total_pages
            elif page_size != page_size_seen or total_pages != total_pages_seen:
                raise WatchError("Artificial Analysis pagination metadata changed during retrieval.")
            if not batch or len(batch) > page_size or (has_more and len(batch) != page_size):
                raise WatchError("Artificial Analysis returned an incomplete page of model records.")
            if any(not isinstance(item, dict) for item in batch):
                raise WatchError("Artificial Analysis page contains malformed model records.")
            records.extend(batch)
            if not has_more:
                if type(version) is not int:
                    raise WatchError("Artificial Analysis omitted its index version.")
                try:
                    save_aa_cache(version, records)
                except OSError:
                    pass
                return version, records
    except WatchError:
        if cache is not None:
            AA_CACHE_STATUS = aa_cache_note(cache["fetched_at"], stale=True)
            return cache["version"], cache["records"]
        raise
    raise WatchError("Artificial Analysis pagination exceeded the configured page limit.")


def resolve_baseline(records: list[dict], spec: dict) -> Decimal:
    wanted = normalize(spec["model"])
    effort = str(spec["effort"]).casefold()
    matches = []
    for record in records:
        if normalize(record.get("name")) != wanted and normalize(record.get("slug")) != wanted:
            continue
        name = str(record.get("name", ""))
        found = re.search(r"\(([^)]*)\)", name)
        level = found.group(1).casefold() if found else ""
        if level == effort:
            matches.append(record)
    if len(matches) != 1:
        raise WatchError(
            f"Configured baseline {safe_report_value(spec['model'])} ({safe_report_value(spec['effort'])}) matched {len(matches)} records."
        )
    score = aa_index_score(matches[0])
    if score is None:
        raise WatchError("Configured baseline has no finite numeric Artificial Analysis score.")
    return score


def load_state() -> dict | None:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def prompt_block(
    decision: dict,
    rows: dict[str, dict],
    config: dict,
    gate_score: Decimal,
    references: list[tuple[str, str, Decimal]],
    proxy_state: dict[str, dict] | None = None,
    previous_proxy_state: dict[str, dict] | None = None,
    promotion_sections: list[str] | None = None,
) -> str:
    now = datetime.now(ZoneInfo(config["timezone"]))
    lines = [
        "AGENT TASK: Free-model watch changes",
        f"Report date: {now.day} {now.strftime('%b %Y')}.",
        f"AA gate: {safe_report_value(config['gate']['model'])} ({safe_report_value(config['gate']['effort'])}), Index {gate_score:g}.",
    ]
    for label, effort, score in references:
        lines.append(f"Reference: {safe_report_value(label)} ({safe_report_value(effort)}), AA Index {score:g}.")
    added_offers = decision.get("added_offers", [])
    removed_offers = decision.get("removed_offers", [])
    updated_offers = decision.get("updated_offers", [])
    added_proxies = decision.get("added_proxies", [])
    removed_proxies = decision.get("removed_proxies", [])
    if added_offers:
        lines.append("\nNew routes above the AA gate:")
        for key in added_offers:
            model_ref, _, score = key.rpartition("|")
            model = rows.get(model_ref, rows.get(key, {}))
            safe_name = safe_report_value(model.get("name") or model_ref, "unknown model")
            safe_source = safe_report_value(model.get("source"), "unknown source")
            safe_id = safe_report_value(model.get("id") or model_ref, "unknown model")
            lines.append(f"- {safe_name} · {safe_source} · `{safe_id}` · AA Index {score}")
    if removed_offers:
        lines.append("\nRoutes no longer above the AA gate:")
        for key in removed_offers:
            model_ref, _, score = key.rpartition("|")
            lines.append(f"- `{model_ref}` · previous AA Index {score}")
    if updated_offers:
        lines.append("\nAA scores updated for routes still above the gate:")
        for key in updated_offers:
            model_ref, _, score = key.rpartition("|")
            model = rows.get(model_ref, rows.get(key, {}))
            safe_name = safe_report_value(model.get("name") or model_ref, "unknown model")
            safe_source = safe_report_value(model.get("source"), "unknown source")
            safe_id = safe_report_value(model.get("id") or model_ref, "unknown model")
            lines.append(f"- {safe_name} · {safe_source} · `{safe_id}` · AA Index {score}")
    proxy_state = proxy_state or {}
    previous_proxy_state = previous_proxy_state or {}
    if added_proxies:
        lines.append("\nRelated-model proxy scores (not measurements of the free routes):")
        for key in added_proxies:
            item = proxy_state.get(key, {})
            score = item.get("proxy_score", "unavailable")
            safe_name = safe_report_value(item.get("name") or key, "unknown model")
            safe_source = safe_report_value(item.get("source"), "unknown source")
            safe_id = safe_report_value(item.get("id") or key, "unknown model")
            safe_proxy = safe_report_value(item.get("proxy_name"), "related model")
            safe_effort = f" ({safe_report_value(item['proxy_effort'])})" if item.get("proxy_effort") else ""
            lines.append(
                f"- {safe_name} · {safe_source} · `{safe_id}` · Proxy: {safe_proxy}{safe_effort}, "
                f"AA Index {safe_report_value(score, 'unavailable')}"
            )
    if removed_proxies:
        lines.append("\nProxy scores no longer available:")
        for key in removed_proxies:
            item = previous_proxy_state.get(key, {})
            safe_name = safe_report_value(item.get("name") or key, "unknown model")
            safe_id = safe_report_value(item.get("id") or key, "unknown model")
            safe_proxy = safe_report_value(item.get("proxy_name"), "related model")
            safe_score = safe_report_value(item.get("proxy_score"), "unavailable")
            lines.append(
                f"- {safe_name} · `{safe_id}` · previous proxy: {safe_proxy} "
                f"(AA Index {safe_score})"
            )
    if promotion_sections:
        lines.extend(["", *promotion_sections])
    return sanitize_report_text("\n".join(lines))


def render_chart(
    offers: list[dict],
    proxy_rows: list[tuple[dict, dict]],
    baselines: list[tuple[str, str, Decimal]],
    version: int,
    out_path: Path,
) -> str | None:
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None
    if not offers and not proxy_rows:
        return None

    direct_rows = sorted(offers, key=lambda item: -item["score"])[:MAX_CHART_ROWS]
    related_rows = sorted(proxy_rows, key=lambda pair: -pair[1]["score"])[:MAX_CHART_ROWS]
    chart_truncated = len(offers) > len(direct_rows) or len(proxy_rows) > len(related_rows)
    width, left, right = 1180, 390, 1060
    top, bottom, direct_height, proxy_height = 72, 100, 34, 30
    proxy_heading = 32 if related_rows else 0
    height = top + direct_height * len(direct_rows) + proxy_heading + proxy_height * len(related_rows) + bottom
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    small = ImageFont.load_default()
    all_scores = [float(score) for _, _, score in baselines]
    all_scores.extend(float(item["score"]) for item in direct_rows)
    all_scores.extend(float(proxy["score"]) for _, proxy in related_rows)
    max_score = max(all_scores + [10.0])
    max_score = max(max_score * 1.08, 10.0)

    def x_position(value: Decimal | int | float) -> int:
        return int(left + (right - left) * float(value) / max_score)

    def safe_text(value: object) -> str:
        return safe_report_value(value).encode("latin-1", "replace").decode("latin-1")

    draw.text((14, 18), safe_text(f"Artificial Analysis Intelligence Index · v{version}"), font=font, fill="#111827")
    tick_count = 8
    for index in range(tick_count + 1):
        tick = max_score * index / tick_count
        tick_x = x_position(tick)
        draw.line((tick_x, top - 8, tick_x, height - bottom + 4), fill="#e5e7eb", width=1)
        draw.text((tick_x + 2, top - 24), safe_text(f"{tick:g}"), font=small, fill="#6b7280")
    for index, (label, effort, score) in enumerate(baselines):
        x = x_position(score)
        color = "#b91c1c" if index == 0 else "#6b7280"
        for y in range(top - 6, height - bottom + 4, 10):
            draw.line((x, y, x, min(y + 5, height - bottom + 4)), fill=color, width=2)

    for index, offer in enumerate(direct_rows):
        y = top + index * direct_height
        label = safe_text(f"{offer.get('name') or offer.get('id')} · {offer.get('source')}")[:48]
        end_x = x_position(offer["score"])
        draw.text((14, y + 9), label, font=font, fill="#111827")
        draw.rectangle((left, y + 4, end_x, y + 26), fill="#2563eb")
        draw.text((min(end_x + 6, right + 6), y + 9), f"{offer['score']:g}", font=small, fill="#111827")

    y = top + direct_height * len(direct_rows)
    if related_rows:
        draw.text(
            (14, y + 7),
            safe_text("Related-model proxy scores · not measurements of the free routes"),
            font=font,
            fill="#9a3412",
        )
        y += proxy_heading
    for model, proxy in related_rows:
        label = safe_text(f"{model.get('name') or model.get('id')} → {proxy['name']}")[:48]
        end_x = x_position(proxy["score"])
        draw.text((14, y + 7), label, font=small, fill="#7c2d12")
        draw.rectangle((left, y + 4, end_x, y + 23), fill="#f97316")
        draw.text((min(end_x + 6, right + 6), y + 7), f"{proxy['score']:g}", font=small, fill="#111827")
        y += proxy_height

    legend = "  ·  ".join(f"{label} ({effort}) {score:g}" for label, effort, score in baselines)
    draw.text((14, height - bottom + 20), safe_text(legend)[:140], font=small, fill="#374151")
    draw.text((14, height - bottom + 42), "Blue: direct AA score   Orange: related-model proxy", font=small, fill="#374151")
    if chart_truncated:
        draw.text((14, height - bottom + 62), f"Chart limited to {MAX_CHART_ROWS} rows per section.", font=small, fill="#6b7280")
    temporary = None
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(out_path.parent, 0o700)
        with tempfile.NamedTemporaryFile(suffix=".png", prefix=".chart-", dir=out_path.parent, delete=False) as handle:
            temporary = Path(handle.name)
        image.save(temporary, format="PNG")
        os.chmod(temporary, 0o600)
        os.replace(temporary, out_path)
    except (OSError, ValueError, OverflowError):
        return None
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass
    return str(out_path)


def run_watch(
    config: dict,
    catalog_fetcher=fetch_catalog,
    aa_fetcher=fetch_aa,
    state: dict | None = None,
    now: datetime | None = None,
    route_reader=read_model_chain,
    generate_chart: bool = False,
) -> tuple[str, dict]:
    global AA_CACHE_STATUS
    AA_CACHE_STATUS = ""
    now = now or datetime.now(ZoneInfo(config["timezone"]))
    previous_state = state if state is not None else load_state()
    previous_notices = (previous_state or {}).get("promotion_notices") or {}
    promotions = config.get("promotions") or []
    due_promotions = [
        item for item in promotions
        if (date.fromisoformat(item["ends"]) - now.date()).days in (0, 1)
    ]
    due_promotion_routes = {(item["source"], item["model_id"]) for item in due_promotions}
    configured_routes = route_reader() if due_promotions else None
    promotion_sections, promotion_notices = promotion_alerts(
        promotions, now, previous_notices, configured_routes
    )

    failures, models = [], []
    for source in config["sources"]:
        try:
            models.extend({**model, "source": source} for model in catalog_fetcher(source))
        except WatchError as exc:
            failures.append(f"{source}: {exc}")
    try:
        version, aa_records = aa_fetcher()
    except WatchError as exc:
        failures.append(f"Artificial Analysis: {exc}")
        version, aa_records = 0, []
    if failures:
        signature = " | ".join(failures)
        same_error = previous_state and previous_state.get("error_signature") == signature
        error_state = dict(previous_state or {})
        error_state.update(
            {"error_signature": signature, "promotion_notices": promotion_notices}
        )
        atomic_json(state_path(), error_state)
        sections = []
        if promotion_sections:
            sections.append(
                "AGENT TASK: Watch incomplete\n" + "\n".join(failures) + "\nNo baseline was advanced."
            )
            sections.extend(promotion_sections_with_unavailable_alternatives(promotion_sections))
        elif not same_error:
            sections.append(
                "AGENT TASK: Watch incomplete\n" + "\n".join(failures) + "\nNo baseline was advanced."
            )
        output = "\n\n".join(sections) if sections else '{"wakeAgent": false}'
        return append_aa_cache_status(output), error_state

    try:
        gate_score = resolve_baseline(aa_records, config["gate"])
        reference_scores = [
            (str(spec.get("label") or spec["model"]), str(spec["effort"]), resolve_baseline(aa_records, spec))
            for spec in config["references"]
        ]
    except WatchError as exc:
        safe_exc = safe_report_value(exc, limit=500)
        signature = f"Artificial Analysis baseline: {safe_exc}"
        same_error = previous_state and previous_state.get("error_signature") == signature
        error_state = dict(previous_state or {})
        error_state.update(
            {"error_signature": signature, "promotion_notices": promotion_notices}
        )
        atomic_json(state_path(), error_state)
        sections = []
        if promotion_sections:
            sections.append(f"AGENT TASK: Watch incomplete\n{safe_exc}\nNo baseline was advanced.")
            sections.extend(promotion_sections_with_unavailable_alternatives(promotion_sections))
        elif not same_error:
            sections.append(f"AGENT TASK: Watch incomplete\n{safe_exc}\nNo baseline was advanced.")
        output = "\n\n".join(sections) if sections else '{"wakeAgent": false}'
        return append_aa_cache_status(output), error_state

    scores = aa_score_map(aa_records, models)
    qualifying, unbenchmarked = classify_models(models, scores, gate_score)
    promotion_sections = add_qualifying_alternatives(
        promotion_sections, qualifying, expiring_routes=due_promotion_routes
    )
    preferred_effort = str(config.get("proxy_effort") or config["gate"]["effort"])
    proxies = proxy_snapshot(unbenchmarked, aa_records, preferred_effort)
    current = {
        "schema": SCHEMA,
        "aa_version": version,
        "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "offers": sorted(f"{model_key(x)}|{score_label(x['score'])}" for x in qualifying),
        "proxies": proxies,
        "promotion_notices": promotion_notices,
        "error_signature": "",
    }
    previous = previous_state
    if previous and previous.get("config_hash") != current["config_hash"]:
        previous = None
    decision = snapshot_decision(previous, current, version)
    if decision["rebaseline"]:
        atomic_json(state_path(), current)
        if not promotion_sections:
            return '{"wakeAgent": false}', current
    elif not decision["wake_agent"] and not promotion_sections:
        atomic_json(state_path(), current)
        return '{"wakeAgent": false}', current

    rows = {model_key(x): x for x in models}
    rows.update({f"{model_key(x)}|{score_label(x['score'])}": x for x in qualifying})
    output = prompt_block(
        decision,
        rows,
        config,
        gate_score,
        reference_scores,
        proxy_state=proxies,
        previous_proxy_state=(previous or {}).get("proxies") or {},
        promotion_sections=promotion_sections,
    )
    output = append_aa_cache_status(output)
    atomic_json(state_path(), current)
    if generate_chart:
        expected_chart_path = None
        image_path = None
        try:
            proxy_rows = []
            for model in unbenchmarked:
                proxy = proxy_for(model, aa_records, preferred_effort)
                if proxy is not None:
                    proxy_rows.append((model, proxy))
            gate_label = safe_report_value(config["gate"].get("label") or config["gate"]["model"])
            chart_baselines = [(gate_label, safe_report_value(config["gate"]["effort"]), gate_score), *reference_scores]
            expected_chart_path = chart_path(now)
            image_path = render_chart(
                qualifying, proxy_rows, chart_baselines, version, expected_chart_path
            )
        except Exception:
            image_path = None
        if image_path and expected_chart_path is not None:
            try:
                media_path = Path(image_path).resolve()
                expected_path = expected_chart_path.resolve()
                directive = str(media_path)
                if media_path == expected_path and all(char.isprintable() and char not in "\r\n" for char in directive):
                    output = f"{output}\nMEDIA:{directive}"
            except (OSError, ValueError, TypeError):
                pass
    return output, current


def main() -> int:
    try:
        config = load_config()
        output, _ = run_watch(config, generate_chart=True)
        print(output)
    except WatchError as exc:
        print(sanitize_report_text(f"AGENT TASK: Watch incomplete\n{exc}\nNo change decision was made."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
