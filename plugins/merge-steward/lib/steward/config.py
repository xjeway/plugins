"""Load `.claude/steward.json`; its presence is what enables the steward in a repository."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .errors import StewardError
from .paths import Repo

CONFIG_REL = os.path.join(".claude", "steward.json")
APPROVAL_MODES = ("risky", "always", "never")


class ConfigError(StewardError):
    pass


@dataclass
class Config:
    verify: List[str]
    integration: str = "integration"
    main: str = "main"
    verify_timeout: int = 1800
    hot_files: Dict[str, str] = field(default_factory=dict)
    approval_mode: str = "risky"
    risky_paths: List[str] = field(default_factory=list)
    risky_when_deleting: bool = True
    rules: List[str] = field(default_factory=list)
    every_n_merges: int = 5


def config_path(repo: Repo) -> Optional[str]:
    """The main checkout's copy wins: steward policy is repository-wide, not per branch."""
    for base in (repo.main_checkout, repo.toplevel):
        p = os.path.join(base, CONFIG_REL)
        if os.path.isfile(p):
            return p
    return None


def load_config(repo: Repo) -> Optional[Config]:
    p = config_path(repo)
    if p is None:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"{p}: {e}") from e
    return parse_config(raw, p)


def _valid_hot_rule(rule: object) -> bool:
    if rule == "union":
        return True
    return isinstance(rule, str) and rule.startswith("regenerate:") and bool(rule[len("regenerate:"):].strip())


def _section(raw: dict, key: str, source: str) -> dict:
    value = raw.get(key, {})
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: '{key}' must be an object")
    return value


def _str_list(section: dict, key: str, source: str) -> List[str]:
    value = section.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{source}: '{key}' must be a list of strings")
    return list(value)


def _positive_int(section: dict, key: str, default: int, source: str) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{source}: '{key}' must be a positive integer")
    return value


def parse_config(raw: dict, source: str = "steward.json") -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: top level must be a JSON object")
    verify = raw.get("verify")
    if not isinstance(verify, list) or not verify or not all(isinstance(c, str) for c in verify):
        raise ConfigError(f"{source}: 'verify' must be a non-empty list of shell commands")
    hot = _section(raw, "hotFiles", source)
    for path, rule in hot.items():
        if not _valid_hot_rule(rule):
            raise ConfigError(f"{source}: hotFiles[{path!r}] must be 'union' or 'regenerate:<command>'")
    approval = _section(raw, "approval", source)
    mode = approval.get("mode", "risky")
    if mode not in APPROVAL_MODES:
        raise ConfigError(f"{source}: approval.mode must be one of {', '.join(APPROVAL_MODES)}")
    return Config(
        verify=list(verify),
        integration=raw.get("integrationBranch", "integration"),
        main=raw.get("mainBranch", "main"),
        verify_timeout=_positive_int(raw, "verifyTimeoutSec", 1800, source),
        hot_files=dict(hot),
        approval_mode=mode,
        risky_paths=_str_list(approval, "riskyPaths", source),
        risky_when_deleting=bool(approval.get("riskyWhenDeletingFiles", True)),
        rules=_str_list(approval, "rules", source),
        every_n_merges=_positive_int(_section(raw, "promote", source), "everyNMerges", 5, source),
    )
