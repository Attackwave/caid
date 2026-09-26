"""Persist only non-secret model choices; never chat or credentials."""

import json
import os
from pathlib import Path

try:
    from .providers import LOCAL_PROVIDERS, PROVIDERS
except ImportError:
    from providers import LOCAL_PROVIDERS, PROVIDERS


def settings_path():
    appdata = Path(os.environ.get("APPDATA", Path.home() / ".config"))
    return appdata / "CAID" / "provider.json"


def _clean(data):
    if not isinstance(data, dict):
        return {}
    result = {}
    provider = data.get("provider")
    if provider in PROVIDERS:
        result["provider"] = provider
    models = data.get("models")
    if isinstance(models, dict):
        result["models"] = {name: value for name, value in models.items()
                            if name in PROVIDERS and isinstance(value, str) and len(value) <= 160}
    urls = data.get("urls")
    if isinstance(urls, dict):
        result["urls"] = {name: value for name, value in urls.items()
                          if name in LOCAL_PROVIDERS and isinstance(value, str) and len(value) <= 400}
    return result


def load_settings(path=None):
    target = Path(path) if path else settings_path()
    try:
        return _clean(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def save_settings(provider, models, urls, path=None):
    target = Path(path) if path else settings_path()
    data = _clean({"provider": provider, "models": models, "urls": urls})
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, target)
