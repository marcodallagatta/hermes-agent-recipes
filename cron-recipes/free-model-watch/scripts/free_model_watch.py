#!/usr/bin/env python3
"""Watch public free-model catalogs against installer-configured AA baselines.

This script reports catalog and third-party benchmark changes. It does not run
inference against listed models. Configure it before scheduling.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCES = {
    "Nous Portal": "https://inference-api.nousresearch.com/v1/models",
    "Vercel AI Gateway": "https://ai-gateway.vercel.sh/v1/models",
    "OpenRouter": "https://openrouter.ai/api/v1/models",
    "OpenCode Zen": "https://opencode.ai/zen/v1/models",
}
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/language/models/free"
AA_KEY_NAME = "ARTIFICIAL_ANALYSIS_API_KEY"
MAX_AA_PAGES = 25
SCHEMA = 1


class WatchError(RuntimeError):
    """A config, source or response was unavailable or invalid."""


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes").expanduser()


def recipe_dir() -> Path:
    return hermes_home() / "cron" / "free-model-watch"


def config_path() -> Path:
    override = os.environ.get("FREE_MODEL_WATCH_CONFIG")
    return Path(override).expanduser() if override else recipe_dir() / "config.json"


def state_path() -> Path:
    return recipe_dir() / "state.json"


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
    except (TypeError, ValueError):
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
    return config


def normalize(value: object) -> str:
    text = str(value or "").casefold()
    text = re.sub(r"\bfree\b", " ", text)
    return " ".join(re.findall(r"[a-z0-9]+", text))


def is_free_model(source: str, record: dict) -> bool:
    model_id = str(record.get("id", ""))
    if source == "Vercel AI Gateway":
        return record.get("type") == "language" and "free" in (record.get("tags") or [])
    if source == "OpenCode Zen":
        return model_id.endswith("-free") or model_id.endswith(":free")
    return model_id.endswith(":free")


def model_key(model: dict) -> str:
    return f"{model['source']}|{model['id']}"


def aa_score_map(records: list[dict], models: list[dict]) -> dict[str, Decimal | None]:
    by_name: dict[str, list[dict]] = {}
    for record in records:
        for key in (normalize(record.get("name")), normalize(record.get("slug"))):
            if key:
                by_name.setdefault(key, []).append(record)
    result: dict[str, Decimal | None] = {}
    for model in models:
        model_id = str(model.get("id", ""))
        leaf = model_id.split("/")[-1].split(":", 1)[0]
        leaf = re.sub(r"-(free|preview|flash)$", "", leaf, flags=re.I)
        candidates = {normalize(model.get("name")), normalize(leaf)}
        matches: dict[str, dict] = {}
        for candidate in candidates - {""}:
            for record in by_name.get(candidate, []):
                identifier = str(record.get("id") or record.get("slug") or record.get("name"))
                matches[identifier] = record
        record = next(iter(matches.values())) if len(matches) == 1 else None
        raw_score = ((record or {}).get("evaluations") or {}).get("artificial_analysis_intelligence_index")
        try:
            score = Decimal(str(raw_score)) if raw_score is not None else None
        except (InvalidOperation, ValueError):
            score = None
        result[model_key(model)] = score
    return result


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
    if not previous or previous.get("schema") != SCHEMA or previous.get("aa_version") != aa_version:
        return {"wake_agent": False, "rebaseline": True, "added_offers": [], "removed_offers": [], "added_unbenchmarked": [], "removed_unbenchmarked": []}
    old_offers = set(previous.get("offers") or [])
    new_offers = set(current.get("offers") or [])
    old_unbenchmarked = set(previous.get("unbenchmarked") or [])
    new_unbenchmarked = set(current.get("unbenchmarked") or [])
    additions = sorted(new_offers - old_offers)
    removals = sorted(old_offers - new_offers)
    new_unknown = sorted(new_unbenchmarked - old_unbenchmarked)
    gone_unknown = sorted(old_unbenchmarked - new_unbenchmarked)
    return {
        "wake_agent": bool(additions or removals or new_unknown or gone_unknown),
        "rebaseline": False,
        "added_offers": additions,
        "removed_offers": removals,
        "added_unbenchmarked": new_unknown,
        "removed_unbenchmarked": gone_unknown,
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


def get_json(url: str, headers: dict[str, str] | None = None) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Hermes-Cron-Recipe/1.0", **(headers or {})})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
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
    return [x for x in data if isinstance(x, dict) and is_free_model(source, x)]


def fetch_aa() -> tuple[int, list[dict]]:
    key = read_key()
    records, version = [], None
    for page in range(1, MAX_AA_PAGES + 1):
        payload = get_json(f"{AA_MODELS_URL}?page={page}", {"x-api-key": key})
        if version is None:
            version = payload.get("intelligence_index_version")
        batch, pagination = payload.get("data"), payload.get("pagination")
        if not isinstance(batch, list) or not isinstance(pagination, dict):
            raise WatchError("Artificial Analysis returned an incomplete page.")
        records.extend(item for item in batch if isinstance(item, dict))
        if not pagination.get("has_more"):
            if not isinstance(version, int):
                raise WatchError("Artificial Analysis omitted its index version.")
            return version, records
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
        raise WatchError(f"Configured baseline {spec['model']} ({spec['effort']}) matched {len(matches)} records.")
    raw = ((matches[0].get("evaluations") or {}).get("artificial_analysis_intelligence_index"))
    try:
        score = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        raise WatchError("Configured baseline has no numeric Artificial Analysis score.") from None
    return score


def load_state() -> dict | None:
    try:
        value = json.loads(state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def prompt_block(decision: dict, rows: dict[str, dict], config: dict, gate_score: Decimal, references: list[tuple[str, str, Decimal]]) -> str:
    now = datetime.now(ZoneInfo(config["timezone"]))
    lines = ["AGENT TASK: Free-model catalog and benchmark changes", f"Report date: {now.day} {now.strftime('%b %Y')}.", f"Configured gate: {config['gate']['model']} ({config['gate']['effort']}), AA Index {gate_score:g}."]
    for label, effort, score in references:
        lines.append(f"Reference {label} ({effort}): AA Index {score:g}.")
    for title, keys in (("New qualifying offers", decision["added_offers"]), ("Removed qualifying offers", decision["removed_offers"]), ("New unbenchmarked routes", decision["added_unbenchmarked"]), ("No longer unbenchmarked", decision["removed_unbenchmarked"])):
        if keys:
            lines.append(f"\n{title}:")
            for key in keys:
                model_ref, _, score = key.rpartition("|")
                model = rows.get(model_ref, rows.get(key, {}))
                detail = f"AA Index {score}" if "qualifying" in title else "No comparable AA score"
                lines.append(f"- {model.get('name', key)} · {model.get('source', 'unknown source')} · `{model.get('id', model_ref)}` · {detail}")
    lines.append("\nUse only the printed scores for benchmark claims. Do not imply this watch ran the models. Research unbenchmarked routes briefly using the attached prompt instructions.")
    return "\n".join(lines)


def run_watch(config: dict, catalog_fetcher=fetch_catalog, aa_fetcher=fetch_aa, state: dict | None = None) -> tuple[str, dict]:
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
        previous_state = state if state is not None else load_state()
        signature = " | ".join(failures)
        if previous_state and previous_state.get("error_signature") == signature:
            return '{"wakeAgent": false}', previous_state
        error_state = dict(previous_state or {})
        error_state.update({"schema": SCHEMA, "error_signature": signature})
        atomic_json(state_path(), error_state)
        output = "AGENT TASK: Watch incomplete" + chr(10) + chr(10).join(failures) + chr(10) + "No baseline was advanced."
        return output, error_state

    gate_score = resolve_baseline(aa_records, config["gate"])
    reference_scores = [
        (str(spec.get("label") or spec["model"]), str(spec["effort"]), resolve_baseline(aa_records, spec))
        for spec in config["references"]
    ]
    scores = aa_score_map(aa_records, models)
    qualifying, unbenchmarked = classify_models(models, scores, gate_score)
    current = {
        "schema": SCHEMA,
        "aa_version": version,
        "config_hash": hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest(),
        "offers": sorted(f"{model_key(x)}|{x['score']:g}" for x in qualifying),
        "unbenchmarked": sorted(model_key(x) for x in unbenchmarked),
        "error_signature": "",
    }
    previous = state if state is not None else load_state()
    if previous and previous.get("config_hash") != current["config_hash"]:
        previous = None
    decision = snapshot_decision(previous, current, version)
    if decision["rebaseline"]:
        atomic_json(state_path(), current)
        return '{"wakeAgent": false}', current
    if not decision["wake_agent"]:
        atomic_json(state_path(), current)
        return '{"wakeAgent": false}', current
    rows = {model_key(x): x for x in models}
    rows.update({f"{model_key(x)}|{x['score']:g}": x for x in qualifying})
    output = prompt_block(decision, rows, config, gate_score, reference_scores)
    atomic_json(state_path(), current)
    return output, current


def main() -> int:
    try:
        config = load_config()
        output, _ = run_watch(config)
        print(output)
    except WatchError as exc:
        print(f"AGENT TASK: Watch incomplete\n{exc}\nNo change decision was made.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
