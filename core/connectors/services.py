"""Configuration-aware service connectors.

These adapters deliberately do not pretend to be connected. They provide the
capability contract and world-model status immediately; provider-specific OAuth
or API clients can be attached without changing the registry or planner.
"""
from __future__ import annotations

import os
from typing import Any

from .base import Connector, ConnectorStatus


class ServiceConnector(Connector):
    env_key = ""
    provider = "configured-service"

    def health(self) -> ConnectorStatus:
        configured = bool(self.context.config.get(self.env_key) or os.getenv(self.env_key)) if self.env_key else False
        if not self.enabled:
            state, message = "disabled", "disabled by configuration"
        elif configured:
            state, message = "ready", f"{self.provider} credentials are configured"
        else:
            state, message = "degraded", f"configure {self.env_key} to connect {self.provider}"
        return ConnectorStatus(self.name, state, message, capabilities=list(self.capabilities))

    def observe(self) -> list[dict[str, Any]]:
        status = self.health()
        return [{
            "subject": self.name,
            "predicate": "connection",
            "value": {"state": status.state, "provider": self.provider, "message": status.message},
            "permission_scope": f"{self.name}.read",
            "confidence": 1.0,
        }]


class CalendarConnector(ServiceConnector):
    name = "calendar"
    provider = "calendar provider"
    env_key = "HERMUS_CALENDAR_TOKEN"
    capabilities = ("calendar.read", "calendar.create", "calendar.update")


class EmailConnector(ServiceConnector):
    name = "email"
    provider = "email provider"
    env_key = "HERMUS_EMAIL_TOKEN"
    capabilities = ("email.read", "email.draft", "email.send")


class GitHubConnector(ServiceConnector):
    name = "github"
    provider = "GitHub"
    env_key = "GITHUB_TOKEN"
    capabilities = ("github.read", "github.branch", "github.pull_request", "github.issue")


class WalletConnector(ServiceConnector):
    name = "wallet"
    provider = "agent wallet"
    env_key = "HERMUS_WALLET_TOKEN"
    capabilities = ("wallet.balance", "wallet.ledger", "wallet.payout")


class HostingConnector(ServiceConnector):
    name = "hosting"
    provider = "hosting provider"
    env_key = "HERMUS_HOSTING_TOKEN"
    capabilities = ("hosting.read", "hosting.deploy", "hosting.logs")


SERVICE_CONNECTORS = (CalendarConnector, EmailConnector, GitHubConnector, WalletConnector, HostingConnector)
