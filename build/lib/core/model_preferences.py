"""Backward-compatible import path for the canonical model preferences store."""

from .models.model_preferences import ModelPreferences, model_preferences

__all__ = ["ModelPreferences", "model_preferences"]
