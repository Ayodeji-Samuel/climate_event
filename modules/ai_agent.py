"""
modules/ai_agent.py
Multi-tool AI agent powered by OpenRouter (OpenAI-compatible).
Falls back to a rule-based responder when no API key is configured.

Agent capabilities:
  • analyze_flood_risk(region)  – live GEE flood analysis
  • list_active_alerts()        – pull current alert table
  • explain_risk_level(level)   – educational context
  • summarize_stats(stats_dict) – human-readable summary
  • recommend_action(risk)      – response recommendation

Background scheduler: re-runs flood checks for monitored regions
every MONITOR_INTERVAL seconds and auto-creates alerts.
"""

import json
import logging
import threading
import time
from datetime import datetime
from typing import Dict, Any, Optional, List

import requests as _requests

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are ANSA, the AI assistant for ANSASphere — a real-time environmental "
    "intelligence platform powered by Google Earth Engine satellite data. "
    "You have tools to run live analyses for 15 environmental layers: "
    "flood risk (Sentinel-1 SAR), vegetation (NDVI), land cover, heatwaves, "
    "active fires, land surface temperature, soil moisture, ground deformation, "
    "forest structure, elevation/terrain, rainfall (CHIRPS), snow cover, "
    "crop stress, air pollution (NO\u2082), sea level/water storage, and glaciers. "
    "You can also check active alerts and recommend emergency actions. "
    "Be concise, precise, and action-oriented. Always cite the satellite dataset and date. "
    "When risk is elevated, prioritise life-safety information."
)

# ---------------------------------------------------------------------------
# Tool definitions (OpenAI function-calling format)
# ---------------------------------------------------------------------------
TOOL_DECLARATIONS = [
    {
        "name": "analyze_flood_risk",
        "description": (
            "Run a real-time Sentinel-1 SAR flood analysis for a given region "
            "and return flood extent, area statistics, and risk level."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "region_id": {
                    "type": "string",
                    "description": "Region identifier e.g. 'nigeria', 'kenya', 'bangladesh'",
                },
                "event_date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD) for the analysis window. Defaults to today.",
                },
            },
            "required": ["region_id"],
        },
    },
    {
        "name": "list_active_alerts",
        "description": "Return the current list of active flood alerts in the system.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "explain_risk_level",
        "description": "Provide an educational explanation for a flood risk level (0–4).",
        "parameters": {
            "type": "object",
            "properties": {
                "level": {"type": "integer", "description": "Risk level 0–4"}
            },
            "required": ["level"],
        },
    },
    {
        "name": "recommend_action",
        "description": "Return recommended response actions for a given flood risk level.",
        "parameters": {
            "type": "object",
            "properties": {
                "risk_level": {"type": "integer", "description": "Risk level 0–4"},
                "region": {"type": "string", "description": "Region name"},
            },
            "required": ["risk_level", "region"],
        },
    },
    {
        "name": "analyze_climate_layer",
        "description": (
            "Run a real-time environmental analysis for one of 15 satellite-derived layers: "
            "vegetation (NDVI), land_cover, heatwaves, fires, temperature (LST), "
            "soil_moisture, deformation (InSAR proxy), forest_structure, elevation, "
            "rainfall (CHIRPS), snow, crop_stress, pollution (NO2), sea_level (GRACE), "
            "glacier. Returns statistics and a map tile URL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {
                    "type": "string",
                    "description": (
                        "One of: vegetation, land_cover, heatwaves, fires, temperature, "
                        "soil_moisture, deformation, forest_structure, elevation, rainfall, "
                        "snow, crop_stress, pollution, sea_level, glacier"
                    ),
                },
                "region_id": {
                    "type": "string",
                    "description": "Region identifier e.g. 'nigeria', 'kenya', 'bangladesh'",
                },
                "date": {
                    "type": "string",
                    "description": "ISO date (YYYY-MM-DD). Defaults to today.",
                },
            },
            "required": ["layer_id", "region_id"],
        },
    },
    {
        "name": "list_climate_layers",
        "description": "Return the catalogue of all 15 available environmental monitoring layers.",
        "parameters": {"type": "object", "properties": {}},
    },
]

# Build OPENROUTER_TOOLS from TOOL_DECLARATIONS (done after the list is fully defined)
OPENROUTER_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
    for t in TOOL_DECLARATIONS
]

# Risk explanations (fallback)
RISK_EXPLANATIONS = {
    0: "No significant flooding detected. Normal conditions prevail.",
    1: (
        "WATCH: Minor flooding possible in low-lying areas. "
        "Less than 5 % of the region shows anomalous water extent. "
        "Monitor conditions and stay informed."
    ),
    2: (
        "ADVISORY: Moderate flooding likely. 5–15 % of the region shows elevated "
        "water extent compared to baseline. Prepare emergency kits; avoid flood-prone routes."
    ),
    3: (
        "WARNING: Significant flooding underway. 15–30 % of the region is affected. "
        "Evacuate low-lying areas. Activate local emergency response protocols."
    ),
    4: (
        "EMERGENCY: Catastrophic flooding. Over 30 % of the region is inundated. "
        "Immediate evacuation required. Coordinate with national disaster agencies."
    ),
}

RECOMMENDED_ACTIONS = {
    0: ["Continue routine monitoring", "Maintain flood preparedness plans"],
    1: [
        "Increase monitoring frequency",
        "Alert local emergency coordinators",
        "Check drainage infrastructure",
        "Advise communities in flood-prone areas to stay vigilant",
    ],
    2: [
        "Activate early-warning systems",
        "Pre-position relief supplies",
        "Open evacuation shelters",
        "Issue public advisory via SMS and radio",
        "Inspect and reinforce river embankments",
    ],
    3: [
        "Issue mandatory evacuation orders for high-risk zones",
        "Deploy search-and-rescue teams",
        "Activate national emergency operations centre",
        "Request military/NGO support",
        "Establish temporary medical posts",
    ],
    4: [
        "Declare national disaster",
        "Request international humanitarian assistance",
        "Aerial rescue operations for stranded communities",
        "Mass casualty management protocols",
        "Full mobilisation of all emergency services",
    ],
}


class AIAgent:
    """
    Conversational AI agent with tool-calling capability.
    Detects Gemini availability at init time; degrades gracefully.
    """

    def __init__(self, flood_detector, alert_system,
                 openrouter_api_key: str = "",
                 openrouter_model: str = "openai/gpt-4o-mini",
                 climate_layers=None):
        self.detector = flood_detector
        self.alerts = alert_system
        self.climate = climate_layers   # ClimateLayerAnalyzer (may be None)
        self.api_key = openrouter_api_key
        self._model = openrouter_model
        self._ai_available = False
        self._chat_histories: Dict[str, List[Dict]] = {}   # session_id → message list
        self._lock = threading.Lock()

        # Background monitor state
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running = False
        self._monitored_regions: List[str] = ["nigeria", "kenya", "bangladesh"]
        self._last_monitor_run: Optional[str] = None

        self._init_openrouter()

    # ------------------------------------------------------------------
    # OpenRouter initialisation
    # ------------------------------------------------------------------

    def _init_openrouter(self):
        if not self.api_key:
            logger.info("No OpenRouter API key – using rule-based agent fallback.")
            return
        try:
            # Verify connectivity with a lightweight models check
            resp = _requests.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=10,
            )
            resp.raise_for_status()
            self._ai_available = True
            logger.info("OpenRouter agent initialised (model=%s).", self._model)
        except Exception as exc:
            logger.warning("OpenRouter init failed: %s — using rule-based fallback.", exc)
            self._ai_available = False

    @property
    def has_ai(self) -> bool:
        return self._ai_available

    # ------------------------------------------------------------------
    # Chat API
    # ------------------------------------------------------------------

    def chat(self, message: str, session_id: str = "default") -> Dict[str, Any]:
        """Process a user message and return agent response + any tool results."""
        if self.has_ai:
            return self._openrouter_chat(message, session_id)
        return self._rule_based_chat(message)

    # ------------------------------------------------------------------
    # OpenRouter multi-turn chat with tool calling
    # ------------------------------------------------------------------

    def _openrouter_chat(self, message: str, session_id: str) -> Dict[str, Any]:
        with self._lock:
            if session_id not in self._chat_histories:
                self._chat_histories[session_id] = []
            history = self._chat_histories[session_id]

        history.append({"role": "user", "content": message})
        tool_results = []

        # Agentic loop – max 5 tool rounds
        for _ in range(5):
            try:
                resp = _requests.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "HTTP-Referer": "https://ansasphere.app",
                        "X-Title": "ANSASphere",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + history,
                        "tools": OPENROUTER_TOOLS,
                        "tool_choice": "auto",
                    },
                    timeout=45,
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.exception("OpenRouter API error: %s", exc)
                return self._rule_based_chat(message)

            choice = data["choices"][0]["message"]

            # No tool calls → final response
            if not choice.get("tool_calls"):
                content = choice.get("content") or ""
                history.append({"role": "assistant", "content": content})
                return {
                    "response": content,
                    "tool_calls": tool_results,
                    "engine": f"openrouter/{self._model}",
                }

            # Process tool calls
            history.append(choice)
            for tc in choice["tool_calls"]:
                fn_name = tc["function"]["name"]
                try:
                    fn_args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    fn_args = {}

                result = self._dispatch_tool(fn_name, fn_args)
                tool_results.append({"tool": fn_name, "result": result})
                history.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": json.dumps(result),
                })

        # Fallback if loop exhausted without a text response
        return self._rule_based_chat(message)

    # ------------------------------------------------------------------
    # Rule-based fallback
    # ------------------------------------------------------------------

    def _rule_based_chat(self, message: str) -> Dict[str, Any]:
        msg_lower = message.lower()
        tool_calls = []

        # --- Route to tool based on keywords ---
        if any(w in msg_lower for w in ["flood", "water", "inundation", "risk", "analyze", "check"]):
            region_id = self._extract_region(msg_lower)
            result = self._dispatch_tool("analyze_flood_risk", {"region_id": region_id})
            tool_calls.append({"tool": "analyze_flood_risk", "result": result})

            risk_lvl = result.get("risk", {}).get("level", 0)
            area = result.get("stats", {}).get("flooded_area_km2", 0)
            label = result.get("risk", {}).get("label", "Unknown")
            region_label = result.get("region_label", region_id)
            text = (
                f"**Flood Status — {region_label}**\n\n"
                f"Risk Level: **{label}** (Level {risk_lvl}/4)\n"
                f"Flooded Area: **{area:,.1f} km²**\n\n"
                f"{RISK_EXPLANATIONS.get(risk_lvl, '')}"
            )

        elif any(w in msg_lower for w in ["alert", "warning", "active"]):
            result = self._dispatch_tool("list_active_alerts", {})
            tool_calls.append({"tool": "list_active_alerts", "result": result})
            alerts = result.get("alerts", [])
            if alerts:
                lines = [f"**{a['region']}** — Level {a['level']}: {a['message']}"
                         for a in alerts[:5]]
                text = "**Active Flood Alerts**\n\n" + "\n".join(lines)
            else:
                text = "No active flood alerts at this time."

        elif any(w in msg_lower for w in ["recommend", "action", "do", "response"]):
            region_id = self._extract_region(msg_lower)
            res = self._dispatch_tool("analyze_flood_risk", {"region_id": region_id})
            tool_calls.append({"tool": "analyze_flood_risk", "result": res})
            lvl = res.get("risk", {}).get("level", 0)
            actions = RECOMMENDED_ACTIONS.get(lvl, [])
            text = (
                f"**Recommended Actions for {region_id.title()} (Level {lvl})**\n\n"
                + "\n".join(f"• {a}" for a in actions)
            )

        elif any(w in msg_lower for w in ["explain", "what", "mean", "level"]):
            # Extract risk level number from text
            lvl = 1
            for word in msg_lower.split():
                if word.isdigit():
                    lvl = min(int(word), 4)
                    break
            result = self._dispatch_tool("explain_risk_level", {"level": lvl})
            tool_calls.append({"tool": "explain_risk_level", "result": result})
            text = result.get("explanation", "")

        elif any(w in msg_lower for w in ["hello", "hi", "hey", "help"]):
            text = (
                "Hello! I'm **ANSA**, your AI assistant for ANSASphere.\n\n"
                "I can help you:\n"
                "• **Analyze flood risk** for any region — *'Check flood risk in Nigeria'*\n"
                "• **List active alerts** — *'Show active alerts'*\n"
                "• **Recommend actions** — *'What should we do in Kenya?'*\n"
                "• **Explain risk levels** — *'Explain level 3'*\n\n"
                "What would you like to know?"
            )
        else:
            text = (
                "I can analyze flood risk, list alerts, and recommend emergency actions. "
                "Try asking: *'What is the flood status in Bangladesh?'*"
            )

        return {"response": text, "tool_calls": tool_calls, "engine": "rule-based"}

    # ------------------------------------------------------------------
    # Tool dispatcher
    # ------------------------------------------------------------------

    def _dispatch_tool(self, name: str, args: Dict) -> Dict:
        try:
            if name == "analyze_flood_risk":
                return self.detector.analyze(
                    region_id=args.get("region_id", "nigeria"),
                    event_date=args.get("event_date"),
                )
            if name == "list_active_alerts":
                return {"alerts": self.alerts.get_active()}
            if name == "explain_risk_level":
                lvl = int(args.get("level", 0))
                return {
                    "level": lvl,
                    "label": ["None", "Watch", "Advisory", "Warning", "Emergency"][min(lvl, 4)],
                    "explanation": RISK_EXPLANATIONS.get(lvl, "Unknown level."),
                }
            if name == "recommend_action":
                lvl = int(args.get("risk_level", 0))
                region = args.get("region", "the region")
                return {
                    "risk_level": lvl,
                    "region": region,
                    "actions": RECOMMENDED_ACTIONS.get(lvl, []),
                }
            if name == "analyze_climate_layer":
                if self.climate is None:
                    return {"error": "Climate layer analyser not initialised."}
                return self.climate.analyze(
                    layer_id=args.get("layer_id", "vegetation"),
                    region_id=args.get("region_id", "nigeria"),
                    date=args.get("date"),
                )
            if name == "list_climate_layers":
                if self.climate is None:
                    return {"layers": []}
                return {"layers": self.climate.list_layers()}
        except Exception as exc:
            logger.exception("Tool dispatch error (%s): %s", name, exc)
            return {"error": str(exc)}
        return {"error": f"Unknown tool: {name}"}

    def _extract_region(self, text: str) -> str:
        """Very simple keyword → region_id mapping."""
        from modules.flood_detector import KNOWN_REGIONS
        for region_id in KNOWN_REGIONS:
            if region_id in text:
                return region_id
        # Check for common variants
        mapping = {
            "west africa": "nigeria",
            "east africa": "kenya",
            "south asia": "bangladesh",
        }
        for phrase, rid in mapping.items():
            if phrase in text:
                return rid
        return "nigeria"   # default

    # ------------------------------------------------------------------
    # Background monitor
    # ------------------------------------------------------------------

    def start_monitor(self, regions: Optional[List[str]] = None,
                      interval: int = 3600):
        if regions:
            self._monitored_regions = regions
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop,
            args=(interval,),
            daemon=True,
            name="ansa-monitor",
        )
        self._monitor_thread.start()
        logger.info("Background monitor started (interval=%ds, regions=%s)",
                    interval, self._monitored_regions)

    def stop_monitor(self):
        self._monitor_running = False

    def _monitor_loop(self, interval: int):
        while self._monitor_running:
            try:
                self._run_monitor_cycle()
            except Exception as exc:
                logger.error("Monitor cycle error: %s", exc)
            time.sleep(interval)

    def _run_monitor_cycle(self):
        today = datetime.utcnow().strftime("%Y-%m-%d")
        self._last_monitor_run = datetime.utcnow().isoformat()
        for region_id in self._monitored_regions:
            try:
                result = self.detector.analyze(region_id=region_id,
                                               event_date=today,
                                               use_cache=False)
                risk = result.get("risk", {}).get("level", 0)
                area = result.get("stats", {}).get("flooded_area_km2", 0)

                if risk >= 2:
                    self.alerts.create(
                        region=result.get("region_label", region_id),
                        level=risk,
                        message=(
                            f"Automated detection: {area:,.0f} km² flooded "
                            f"({result['stats'].get('flood_fraction_pct', 0):.1f}% of region). "
                            f"Risk: {result['risk']['label']}."
                        ),
                        source="auto-monitor",
                    )
                    logger.info("Auto-alert created: %s level=%d", region_id, risk)
            except Exception as exc:
                logger.warning("Monitor failed for %s: %s", region_id, exc)

    @property
    def monitor_status(self) -> Dict:
        return {
            "running": self._monitor_running,
            "regions": self._monitored_regions,
            "last_run": self._last_monitor_run,
            "engine": f"openrouter/{self._model}" if self.has_ai else "rule-based",
        }
