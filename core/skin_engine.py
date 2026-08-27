"""Skin Engine - Data-driven CLI theming - Free - Loads custom YAML skins like original Hermes"""

import yaml
from pathlib import Path
from .config import config

# Optional yaml
try:
    import yaml as pyyaml
    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False

class SkinEngine:
    """Skin engine - data-driven theming - free - matches original hermes_cli/skin_engine.py"""

    DEFAULT_SKINS = {
        "default": {
            "name": "default",
            "description": "Classic Hermes — gold and kawaii",
            "colors": {
                "banner_border": "#DAA520",
                "banner_title": "#FFD700",
                "banner_accent": "#B8860B",
                "text": "#FFF8DC",
                "text_dim": "#C9B896",
                "bg": "#0f0e0a",
                "bg_card": "#1a1812",
            },
            "spinner": {
                "thinking_verbs": ["thinking", "pondering", "considering", "musing"],
                "faces": ["(｡♥‿♥｡)", "(◕‿◕)", "(｡•̀ᴗ-)✧", "٩(◕‿◕)۶"],
                "wings": [["⟨", "⟩"], ["|", "|"]]
            },
            "branding": {
                "agent_name": "Hermus Agent",
                "response_label": "Hermus",
                "tool_prefix": "🔧"
            }
        },
        "slate": {
            "name": "slate",
            "description": "Cool blue — developer-focused",
            "colors": {
                "banner_border": "#4169e1",
                "banner_title": "#6a8eff",
                "banner_accent": "#4169e1",
                "text": "#c9d1ff",
                "text_dim": "#8a92c9",
                "bg": "#0a0e1a",
                "bg_card": "#151a2d",
            },
            "spinner": {
                "thinking_verbs": ["analyzing", "processing", "computing"],
                "faces": ["(◕‿◕)", "(｡•̀ᴗ-)✧"],
            },
            "branding": {
                "agent_name": "Hermus Agent",
                "response_label": "Hermus",
            }
        },
        "ares": {
            "name": "ares",
            "description": "War-god theme — crimson and bronze",
            "colors": {
                "banner_border": "#8b0000",
                "banner_title": "#DC143C",
                "banner_accent": "#CD7F32",
                "text": "#ffcccc",
                "text_dim": "#c9a0a0",
                "bg": "#1a0a0a",
                "bg_card": "#2a1515",
            },
            "spinner": {
                "thinking_verbs": ["forging", "marching", "tempering steel", "sharpening blade"],
                "faces": ["(╯°□°）╯", "(ง'̀-'́)ง"],
                "wings": [["⚔️", "🛡️"]],
                "banner_ascii": "sword-and-shield"
            },
            "branding": {
                "agent_name": "Ares Agent",
                "response_label": "Ares",
            }
        },
        "mono": {
            "name": "mono",
            "description": "Monochrome — clean grayscale",
            "colors": {
                "banner_border": "#555555",
                "banner_title": "#888888",
                "banner_accent": "#555555",
                "text": "#c9d1d9",
                "text_dim": "#8a8a8a",
                "bg": "#0a0a0a",
                "bg_card": "#1a1a1a",
            },
            "branding": {
                "agent_name": "Hermus Agent",
            }
        },
        "poseidon": {
            "name": "poseidon",
            "description": "Ocean-god theme — deep blue and seafoam",
            "colors": {
                "banner_border": "#006994",
                "banner_title": "#5F9EA0",
                "banner_accent": "#20B2AA",
                "text": "#c0f0ff",
                "text_dim": "#8ab0c0",
                "bg": "#0a1420",
                "bg_card": "#142030",
            },
            "spinner": {
                "thinking_verbs": ["charting currents", "sounding the depth", "navigating tides"],
                "faces": ["(◕‿◕)", "≈(◕‿◕)≈"],
                "wings": [["🌊", "🌊"]],
                "banner_ascii": "trident"
            },
            "branding": {
                "agent_name": "Poseidon Agent",
            }
        },
        "cyberpunk": {
            "name": "cyberpunk",
            "description": "Neon-soaked terminal theme - example custom skin",
            "colors": {
                "banner_border": "#FF00FF",
                "banner_title": "#00FFFF",
                "banner_accent": "#FF1493",
                "text": "#00FF00",
                "text_dim": "#FF00FF",
                "bg": "#0a0a0a",
                "bg_card": "#1a0a2a",
            },
            "spinner": {
                "thinking_verbs": ["jacking in", "decrypting", "uploading", "bypassing firewall"],
                "wings": [["⟨⚡", "⚡⟩"], ["[", "]"]],
            },
            "branding": {
                "agent_name": "Cyber Agent",
                "response_label": " ⚡ Cyber ",
                "tool_prefix": " "
            }
        }
    }

    def __init__(self, skins_dir: str = None):
        self.skins_dir = Path(skins_dir or config.resolve_path("data/skins"))
        self.skins_dir.mkdir(parents=True, exist_ok=True)
        # Also check ~/.hermes/skins/ like original
        self.user_skins_dir = Path.home() / ".hermes" / "skins"
        self.user_skins_dir.mkdir(parents=True, exist_ok=True)
        self.current_skin = "default"
        self.animations_enabled = True
        self._load_custom_skins()

    def _load_custom_skins(self):
        """Load custom YAML skins from data/skins and ~/.hermes/skins/"""
        # Create example custom skins if not exist
        example_path = self.skins_dir / "cyberpunk.yaml"
        if not example_path.exists() and YAML_AVAILABLE:
            example = {
                "name": "cyberpunk",
                "description": "Neon-soaked terminal theme",
                "colors": {
                    "banner_border": "#FF00FF",
                    "banner_title": "#00FFFF",
                    "banner_accent": "#FF1493"
                },
                "spinner": {
                    "thinking_verbs": ["jacking in", "decrypting", "uploading"],
                    "wings": [["⟨⚡", "⚡⟩"]]
                },
                "branding": {
                    "agent_name": "Cyber Agent",
                    "response_label": " ⚡ Cyber ",
                    "tool_prefix": " "
                }
            }
            try:
                with open(example_path, "w") as f:
                    yaml.safe_dump(example, f)
            except:
                pass

    def list_skins(self) -> list[dict]:
        """List all skins - default + custom YAML"""
        skins = []
        # Default skins
        for name, data in self.DEFAULT_SKINS.items():
            skins.append({
                "name": name,
                "description": data.get("description",""),
                "source": "builtin",
                "colors": data.get("colors", {}),
                "is_custom": False
            })

        # Custom YAML skins from data/skins and ~/.hermes/skins/
        for skins_dir in [self.skins_dir, self.user_skins_dir]:
            if not skins_dir.exists():
                continue
            for yaml_file in skins_dir.glob("*.yaml"):
                try:
                    if YAML_AVAILABLE:
                        with open(yaml_file, "r") as f:
                            data = yaml.safe_load(f)
                            if data and "name" in data:
                                skins.append({
                                    "name": data.get("name"),
                                    "description": data.get("description","Custom skin"),
                                    "source": f"custom:{yaml_file}",
                                    "colors": data.get("colors", {}),
                                    "is_custom": True,
                                    "path": str(yaml_file)
                                })
                    else:
                        # Fallback without yaml lib - just name from filename
                        skins.append({
                            "name": yaml_file.stem,
                            "description": f"Custom skin from {yaml_file}",
                            "source": f"custom:{yaml_file}",
                            "is_custom": True,
                            "path": str(yaml_file)
                        })
                except Exception as e:
                    print(f"Failed to load skin {yaml_file}: {e}")

        return skins

    def get_skin(self, name: str) -> dict:
        """Get skin by name - builtin or custom YAML"""
        # Check builtin first
        if name in self.DEFAULT_SKINS:
            return self.DEFAULT_SKINS[name]

        # Check custom YAML
        for skins_dir in [self.skins_dir, self.user_skins_dir]:
            yaml_path = skins_dir / f"{name}.yaml"
            if yaml_path.exists() and YAML_AVAILABLE:
                try:
                    with open(yaml_path, "r") as f:
                        data = yaml.safe_load(f)
                        return data
                except:
                    pass

        # Fallback to default
        return self.DEFAULT_SKINS.get("default", {})

    def set_skin(self, name: str) -> dict:
        """Set current skin - like /skin command or display.skin config"""
        skin = self.get_skin(name)
        if skin:
            self.current_skin = name
            # Save to config (free)
            try:
                # Update config.yaml if exists, or just remember
                config_path = config.resolve_path("data/config.yaml")
                # For free version, we just store in memory and user_model
                from .memory import memory
                memory.update_user_model({"preferences": {"skin": name}})
            except:
                pass
            return {"success": True, "skin": name, "description": skin.get("description","")}
        return {"success": False, "error": f"Skin {name} not found"}

    def create_custom_skin(self, name: str, description: str, colors: dict, spinner: dict = None, branding: dict = None) -> dict:
        """Create custom YAML skin - free, user can create via CLI"""
        if not YAML_AVAILABLE:
            return {"success": False, "error": "PyYAML not installed. pip install pyyaml"}

        skin_data = {
            "name": name,
            "description": description,
            "colors": colors,
        }
        if spinner:
            skin_data["spinner"] = spinner
        if branding:
            skin_data["branding"] = branding

        yaml_path = self.skins_dir / f"{name}.yaml"
        try:
            with open(yaml_path, "w") as f:
                yaml.safe_dump(skin_data, f)
            return {"success": True, "path": str(yaml_path), "name": name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_current_skin(self) -> dict:
        return self.get_skin(self.current_skin)

    # Animations toggle
    def set_animations_enabled(self, enabled: bool):
        """Toggle animations on/off - free, for accessibility and settings"""
        self.animations_enabled = enabled
        try:
            from .memory import memory
            memory.update_user_model({"preferences": {"animations_enabled": enabled}})
        except:
            pass

    def are_animations_enabled(self) -> bool:
        try:
            from .memory import memory
            model = memory.load_user_model()
            return model.get("preferences", {}).get("animations_enabled", True)
        except:
            return self.animations_enabled

    # Mode persistence - as requested
    def set_mode(self, mode: str) -> dict:
        """Set current mode and persist - agent, chat, multi-agent, multi-chat - free"""
        try:
            from .modes import AgentMode
            mode_normalized = mode.lower().replace("_", "-")
            valid_modes = [m.value for m in AgentMode]
            if mode_normalized not in valid_modes:
                return {"success": False, "error": f"Invalid mode {mode}. Valid: {', '.join(valid_modes)}"}
            
            from .memory import memory
            memory.update_user_model({"preferences": {"mode": mode_normalized}})
            
            return {"success": True, "mode": mode_normalized, "message": f"Mode set to {mode_normalized} and persisted"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def get_current_mode(self) -> str:
        """Get current mode with persistence - loads from user_model if exists"""
        try:
            from .memory import memory
            model = memory.load_user_model()
            return model.get("preferences", {}).get("mode", "agent")
        except:
            return "agent"

    def get_persisted_mode(self) -> str:
        """Get persisted mode from user_model.json"""
        return self.get_current_mode()

# Global skin engine free
skin_engine = SkinEngine()
