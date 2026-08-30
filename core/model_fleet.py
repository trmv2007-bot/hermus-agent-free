"""
Model Fleet — distribute tasks across multiple AI models + API keys.

- Fan-out: same prompt → many models → compare / vote
- Map: split subtasks → each model/key works in parallel → merge
- Race: first successful healthy model wins
- Auto-pick: choose models from discovered healthy keys
"""
from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait, FIRST_COMPLETED
from datetime import datetime

from .multi_key import multi_key_manager
from .providers import get_provider, list_providers


def _split_tasks_from_goal(goal: str, n: int = 3) -> list[str]:
    """Heuristic split; LLM-enhanced when available."""
    # Bullet / numbered lines
    lines = [ln.strip(" -*\t") for ln in goal.splitlines() if ln.strip()]
    bullets = [ln for ln in lines if re.match(r"^(\d+[\).\:]|[-*])\s+", ln) or len(lines) > 1]
    if len(bullets) >= 2:
        cleaned = [re.sub(r"^(\d+[\).\:]|[-*])\s+", "", b).strip() for b in bullets]
        return cleaned[: max(n, len(cleaned))]

    # Sentence split
    parts = re.split(r"(?<=[.!?])\s+|\band then\b|\bthen\b|;|\n", goal, flags=re.I)
    parts = [p.strip() for p in parts if p and len(p.strip()) > 10]
    if len(parts) >= 2:
        return parts[:n]

    # Keyword facets
    facets = [
        f"Research and summarize facts about: {goal}",
        f"List practical steps / implementation for: {goal}",
        f"Risks, alternatives, and recommendation for: {goal}",
    ]
    return facets[:n]


def _available_workers(models: list[str] = None, providers: list[str] = None, limit: int = 8) -> list[dict]:
    """
    Build worker list: {provider, model, key, base_url, name}

    Prefer healthy stored keys with discovered models, then .env-configured
    providers that are visible to the provider resolver.
    """
    workers = []
    entries = multi_key_manager.get_all_entries()
    if providers:
        providers = [p.lower() for p in providers]
        entries = [e for e in entries if e.get("provider") in providers]

    # Add .env-only providers (stored entries are already handled above). This
    # closes the provider-discovery split that made tool routing useless for a
    # user with OPENROUTER_API_KEY / GEMINI_API_KEY / ... in .env but nothing
    # added through `hermus multikey add`.
    try:
        from .provider_resolver import discover_runtime_bundles

        env_bundles = discover_runtime_bundles(include_local=False)
        if providers:
            env_bundles = [b for b in env_bundles if b.get("provider") in providers]
        for b in env_bundles:
            # stored entries are already in ``entries``; only synthetic env
            # bundles carry ``source == 'env'`` here.
            if b.get("source") != "env":
                continue
            entries.append(
                {
                    "provider": b.get("provider"),
                    "key": b.get("key"),
                    "base_url": b.get("base_url"),
                    "default_model": b.get("default_model"),
                    "models": b.get("models") or [],
                    "name": b.get("name"),
                    "rpm_limit": b.get("rpm_limit"),
                    "tpm_limit": b.get("tpm_limit"),
                    "healthy": b.get("healthy"),
                    "health_status": b.get("health_status"),
                }
            )
    except Exception:
        pass

    # Always offer ollama if reachable
    try:
        from .openai_compat import list_models

        ol = list_models("ollama", api_key="")
        if ol.get("success") and ol.get("models"):
            for m in ol["models"][:3]:
                workers.append(
                    {
                        "provider": "ollama",
                        "model": m["id"],
                        "key": "",
                        "base_url": get_provider("ollama").get("base_url"),
                        "name": f"ollama:{m['id']}",
                    }
                )
    except Exception:
        pass

    for e in entries:
        p = (e.get("provider") or "").lower()
        if providers and p not in providers:
            continue
        if get_provider(p).get("retired"):
            continue
        if e.get("healthy") is False and e.get("health_status") == "auth_failed":
            continue
        models_for_key = e.get("models") or []
        default = e.get("default_model") or get_provider(p).get("default_model")
        candidate_models = []
        if models:
            # intersect requested models with known, or use as-is
            for m in models:
                # allow provider/model or bare
                if "/" in m:
                    mp, mn = m.split("/", 1)
                    if mp == p:
                        candidate_models.append(mn)
                else:
                    candidate_models.append(m)
        elif models_for_key:
            candidate_models = models_for_key[:3]
        elif default:
            candidate_models = [default]

        for mid in candidate_models:
            workers.append(
                {
                    "provider": p,
                    "model": mid,
                    "key": e.get("key"),
                    "base_url": e.get("base_url"),
                    "name": e.get("name") or p,
                    "rpm_limit": e.get("rpm_limit"),
                }
            )

    # Explicit models without keys: try env
    if models and not workers:
        for m in models:
            if "/" in m:
                p, mid = m.split("/", 1)
            else:
                p, mid = "ollama", m
            bundle = multi_key_manager.get_key_bundle(p)
            if bundle:
                workers.append(
                    {
                        "provider": p,
                        "model": mid,
                        "key": bundle.get("key"),
                        "base_url": bundle.get("base_url"),
                        "name": bundle.get("name") or p,
                    }
                )

    # Dedupe by provider+model+key tail
    seen = set()
    uniq = []
    for w in workers:
        sig = (w["provider"], w["model"], (w.get("key") or "")[-8:])
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(w)
    return uniq[:limit]


def _run_worker(worker: dict, prompt: str, system: str = None) -> dict:
    from .llm import FreeLLM

    t0 = time.time()
    model_ref = f"{worker['provider']}/{worker['model']}"
    try:
        llm = FreeLLM(
            model_ref,
            api_key=worker.get("key"),
            base_url=worker.get("base_url"),
        )
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = llm.chat(messages)
        ms = int((time.time() - t0) * 1000)
        content = resp.content or ""
        # Detect error-ish
        success = True
        low = content.lower()[:80]
        if low.startswith("⚠️") or "error:" in low[:40] or "no api key" in low:
            success = False
        return {
            "success": success,
            "worker": worker.get("name"),
            "provider": worker["provider"],
            "model": model_ref,
            "response": content,
            "usage": getattr(resp, "usage", {}),
            "latency_ms": ms,
        }
    except Exception as e:
        return {
            "success": False,
            "worker": worker.get("name"),
            "provider": worker.get("provider"),
            "model": f"{worker.get('provider')}/{worker.get('model')}",
            "error": str(e),
            "latency_ms": int((time.time() - t0) * 1000),
        }


class ModelFleet:
    """Distribute work across models and keys."""

    def list_workers(self, models: list[str] = None, providers: list[str] = None, limit: int = 12) -> dict:
        workers = _available_workers(models=models, providers=providers, limit=limit)
        configured = list({e.get("provider") for e in multi_key_manager.get_all_entries()})
        try:
            from .provider_resolver import list_available_providers

            configured = [p["provider"] for p in list_available_providers() if p.get("configured")]
        except Exception:
            pass
        return {
            "workers": [
                {
                    "name": w.get("name"),
                    "provider": w["provider"],
                    "model": w["model"],
                    "has_key": bool(w.get("key")),
                    "base_url": w.get("base_url"),
                }
                for w in workers
            ],
            "count": len(workers),
            "providers_configured": configured,
            "known_presets": [p["id"] for p in list_providers()],
        }

    def fanout(
        self,
        prompt: str,
        models: list[str] = None,
        providers: list[str] = None,
        system: str = None,
        max_workers: int = 6,
        judge: bool = True,
    ) -> dict:
        """Same prompt to many models in parallel → optional judge consensus."""
        workers = _available_workers(models=models, providers=providers, limit=max_workers)
        if not workers:
            return {
                "success": False,
                "error": "No workers available. Add API keys: hermus multikey add --provider groq --key gsk_...",
            }

        results = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(workers))) as ex:
            futs = {ex.submit(_run_worker, w, prompt, system): w for w in workers}
            for fut in as_completed(futs):
                results.append(fut.result())

        results.sort(key=lambda r: (0 if r.get("success") else 1, r.get("latency_ms", 99999)))
        out = {
            "success": any(r.get("success") for r in results),
            "mode": "fanout",
            "prompt": prompt[:500],
            "workers_used": len(workers),
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        if judge and out["success"]:
            out["consensus"] = self._judge(prompt, results)
        return out

    def map_goal(
        self,
        goal: str,
        subtasks: list[str] = None,
        models: list[str] = None,
        providers: list[str] = None,
        max_workers: int = 6,
        merge: bool = True,
    ) -> dict:
        """Split goal into subtasks, assign each to a different model/key, merge."""
        tasks = subtasks or _split_tasks_from_goal(goal, n=max_workers)
        workers = _available_workers(models=models, providers=providers, limit=max(len(tasks), max_workers))
        if not workers:
            return {
                "success": False,
                "error": "No workers available. Add keys via hermus multikey add ...",
            }

        paired = []
        for i, task in enumerate(tasks):
            w = workers[i % len(workers)]
            paired.append((task, w))

        # NOTE: an earlier revision submitted every subtask twice — once into
        # an executor whose results were discarded, then again into the real
        # one below. Each map_goal call therefore spent 2x the tokens/requests.
        results = []
        with ThreadPoolExecutor(max_workers=min(max_workers, len(paired))) as ex:
            futs = {
                ex.submit(_run_worker, w, task, f"Specialist subtask for goal: {goal[:200]}"): (task, w)
                for task, w in paired
            }
            for fut in as_completed(futs):
                task, w = futs[fut]
                r = fut.result()
                r["subtask"] = task
                results.append(r)

        out = {
            "success": any(r.get("success") for r in results),
            "mode": "map",
            "goal": goal,
            "subtasks": tasks,
            "results": results,
            "timestamp": datetime.now().isoformat(),
        }
        if merge and out["success"]:
            out["merged"] = self._merge(goal, results)
        return out

    def race(
        self,
        prompt: str,
        models: list[str] = None,
        providers: list[str] = None,
        max_workers: int = 4,
        timeout: float = 60.0,
    ) -> dict:
        """First successful response wins — cancel others conceptually (we just return first)."""
        workers = _available_workers(models=models, providers=providers, limit=max_workers)
        if not workers:
            return {"success": False, "error": "No workers available"}

        t0 = time.time()
        with ThreadPoolExecutor(max_workers=len(workers)) as ex:
            futs = [ex.submit(_run_worker, w, prompt) for w in workers]
            done, not_done = wait(futs, timeout=timeout, return_when=FIRST_COMPLETED)
            winner = None
            others = []
            for fut in done:
                r = fut.result()
                if r.get("success") and winner is None:
                    winner = r
                else:
                    others.append(r)
            # gather remaining quickly without long wait
            for fut in not_done:
                fut.cancel()
                others.append({"success": False, "status": "cancelled_after_winner"})

        return {
            "success": bool(winner),
            "mode": "race",
            "winner": winner,
            "others": others,
            "elapsed_ms": int((time.time() - t0) * 1000),
        }

    def auto_distribute(self, goal: str, strategy: str = "auto", **kwargs) -> dict:
        """
        strategy:
          - fanout: many models same prompt
          - map: split subtasks
          - race: first wins
          - auto: map if goal looks complex else fanout
        """
        strategy = (strategy or "auto").lower()
        if strategy == "auto":
            complex_score = len(goal) > 120 or any(
                w in goal.lower() for w in (" and ", " then ", "compare", "research", "plan", "multi", "1.", "2.")
            )
            workers = _available_workers(limit=6)
            if len(workers) >= 3 and complex_score:
                strategy = "map"
            elif len(workers) >= 2:
                strategy = "fanout"
            else:
                strategy = "fanout"
        if strategy == "map":
            return self.map_goal(goal, **{k: kwargs[k] for k in ("subtasks", "models", "providers", "max_workers") if k in kwargs})
        if strategy == "race":
            return self.race(goal, **{k: kwargs[k] for k in ("models", "providers", "max_workers") if k in kwargs})
        return self.fanout(goal, **{k: kwargs[k] for k in ("models", "providers", "system", "max_workers", "judge") if k in kwargs})

    def _judge(self, prompt: str, results: list[dict]) -> str:
        ok = [r for r in results if r.get("success") and r.get("response")]
        if not ok:
            return "No successful model responses to judge."
        if len(ok) == 1:
            return ok[0]["response"]
        # Use fastest successful as judge backbone
        judge_worker = {
            "provider": ok[0]["provider"],
            "model": ok[0]["model"].split("/", 1)[-1],
            "key": None,
            "base_url": None,
            "name": "judge",
        }
        # Prefer any configured bundle for that provider
        b = multi_key_manager.get_key_bundle(ok[0]["provider"])
        if b:
            judge_worker["key"] = b.get("key")
            judge_worker["base_url"] = b.get("base_url")
            judge_worker["model"] = b.get("default_model") or judge_worker["model"]

        catalog = "\n\n".join(
            f"### {r.get('model')} ({r.get('latency_ms')}ms)\n{r.get('response','')[:1500]}" for r in ok[:6]
        )
        judge_prompt = (
            f"Original question:\n{prompt}\n\n"
            f"Multiple AI models answered:\n{catalog}\n\n"
            "Synthesize a single best final answer. Note agreements, resolve conflicts, be concise and actionable."
        )
        judged = _run_worker(judge_worker, judge_prompt, system="You are a fair judge merging multi-model answers.")
        return judged.get("response") or ok[0]["response"]

    def _merge(self, goal: str, results: list[dict]) -> str:
        ok = [r for r in results if r.get("success")]
        if not ok:
            return "No successful subtask results."
        body = "\n\n".join(
            f"### Subtask: {r.get('subtask','')}\nModel: {r.get('model')}\n{r.get('response','')[:2000]}"
            for r in ok
        )
        merge_prompt = (
            f"Parent goal: {goal}\n\nSpecialist results:\n{body}\n\n"
            "Merge into one coherent final deliverable with clear sections."
        )
        # Use first ok's provider
        w = {
            "provider": ok[0]["provider"],
            "model": ok[0]["model"].split("/", 1)[-1],
            "key": None,
            "base_url": None,
            "name": "merger",
        }
        b = multi_key_manager.get_key_bundle(ok[0]["provider"])
        if b:
            w.update({"key": b.get("key"), "base_url": b.get("base_url"), "model": b.get("default_model") or w["model"]})
        merged = _run_worker(w, merge_prompt)
        return merged.get("response") or body[:3000]


model_fleet = ModelFleet()
