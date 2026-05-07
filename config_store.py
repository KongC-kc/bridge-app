"""配置存储：JSON 文件位于 %APPDATA%/GLMBridge/config.json"""
import json
import os
import secrets
import threading
import uuid
from pathlib import Path

_lock = threading.Lock()


def _config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "GLMBridge"
    p.mkdir(parents=True, exist_ok=True)
    return p


CONFIG_PATH = _config_dir() / "config.json"


def _default_config() -> dict:
    return {
        "port": 4000,
        "proxy_api_key": "sk-" + secrets.token_hex(24),
        "active_account_id": None,
        "force_model": True,  # 客户端传任何 model 都用 active 账号的 default_model
        "accounts": [],
    }


def load() -> dict:
    with _lock:
        if not CONFIG_PATH.exists():
            cfg = _default_config()
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
            return cfg
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = _default_config()
        # 补全缺失字段
        defaults = _default_config()
        for k, v in defaults.items():
            cfg.setdefault(k, v)
        return cfg


def save(cfg: dict):
    with _lock:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")


def get_active_account(cfg: dict | None = None) -> dict | None:
    cfg = cfg or load()
    aid = cfg.get("active_account_id")
    for acc in cfg.get("accounts", []):
        if acc.get("id") == aid:
            return acc
    return None


def add_account(data: dict) -> dict:
    cfg = load()
    acc = {
        "id": str(uuid.uuid4()),
        "name": data.get("name") or "未命名",
        "provider": data.get("provider") or "custom",
        "api_key": data.get("api_key") or "",
        "api_base": data.get("api_base") or "",
        "default_model": data.get("default_model") or "",
        "enabled": data.get("enabled", True),
    }
    cfg["accounts"].append(acc)
    if not cfg.get("active_account_id"):
        cfg["active_account_id"] = acc["id"]
    save(cfg)
    return acc


def update_account(account_id: str, data: dict) -> dict | None:
    cfg = load()
    for acc in cfg["accounts"]:
        if acc["id"] == account_id:
            for k in ("name", "provider", "api_key", "api_base", "default_model", "enabled"):
                if k in data:
                    acc[k] = data[k]
            save(cfg)
            return acc
    return None


def delete_account(account_id: str) -> bool:
    cfg = load()
    before = len(cfg["accounts"])
    cfg["accounts"] = [a for a in cfg["accounts"] if a["id"] != account_id]
    if cfg.get("active_account_id") == account_id:
        cfg["active_account_id"] = cfg["accounts"][0]["id"] if cfg["accounts"] else None
    save(cfg)
    return len(cfg["accounts"]) < before


def set_active(account_id: str) -> bool:
    cfg = load()
    for acc in cfg["accounts"]:
        if acc["id"] == account_id:
            cfg["active_account_id"] = account_id
            save(cfg)
            return True
    return False


def update_settings(data: dict) -> dict:
    cfg = load()
    for k in ("port", "proxy_api_key", "force_model"):
        if k in data:
            cfg[k] = data[k]
    save(cfg)
    return cfg
