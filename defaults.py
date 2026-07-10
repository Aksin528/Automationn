"""Default tier configuration for self-hosted deployments - unlimited everything."""

from __future__ import annotations
import os
from tracecat.tiers.schemas import EffectiveEntitlements, EffectiveLimits

DEFAULT_TIER_DISPLAY_NAME = "Default"

DEFAULT_LIMITS = EffectiveLimits(
    api_rate_limit=None,
    api_burst_capacity=None,
    max_concurrent_workflows=None,
    max_action_executions_per_workflow=None,
    max_concurrent_actions=None,
)

_AGENT_ADDON_FLAGS = ("agent-approvals", "agent-presets")
_CASE_ADDON_FLAGS = ("case-dropdowns", "case-durations", "case-tasks", "case-triggers")
_RBAC_FLAGS = ("rbac",)

def get_legacy_feature_flags_env() -> str | None:
    return os.environ.get("TRACECAT__FEATURE_FLAGS")

def _parse_feature_flags(feature_flags_env: str) -> set[str]:
    return {
        raw_flag.strip().lower().replace("_", "-")
        for raw_flag in feature_flags_env.split(",")
        if raw_flag.strip()
    }

def resolve_oss_default_entitlements(feature_flags_env: str | None) -> EffectiveEntitlements:
    if not feature_flags_env:
        return EffectiveEntitlements(
            custom_registry=True,
            git_sync=False,
            agent_addons=False,
            case_addons=False,
            rbac_addons=False,
            service_accounts=True,
            workspace_chat=False,
            watchtower=True,
        )

    normalized_flags = _parse_feature_flags(feature_flags_env)
    git_sync_enabled = "git-sync" in normalized_flags
    agent_addons_enabled = any(flag in normalized_flags for flag in _AGENT_ADDON_FLAGS)
    case_addons_enabled = any(flag in normalized_flags for flag in _CASE_ADDON_FLAGS)
    rbac_enabled = any(flag in normalized_flags for flag in _RBAC_FLAGS)

    return EffectiveEntitlements(
        custom_registry=True,
        git_sync=git_sync_enabled,
        agent_addons=agent_addons_enabled,
        case_addons=case_addons_enabled,
        rbac_addons=rbac_enabled,
        service_accounts=True,
        workspace_chat=agent_addons_enabled,
        watchtower=True,
    )

DEFAULT_ENTITLEMENTS = resolve_oss_default_entitlements(get_legacy_feature_flags_env())
