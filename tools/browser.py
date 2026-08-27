"""Browser Automation Playwright Free - No API key, free"""
from pathlib import Path

# Playwright optional - free, no API key
PLAYWRIGHT_AVAILABLE = False
try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False

# Global browser context for reuse
_browser = None
_playwright = None
_page = None

def _ensure_browser():
    global _browser, _playwright, _page
    if not PLAYWRIGHT_AVAILABLE:
        return None, "Playwright not installed. Install free: pip install playwright && playwright install chromium"
    
    if _browser is None:
        try:
            _playwright = sync_playwright().start()
            _browser = _playwright.chromium.launch(headless=True)
            context = _browser.new_context()
            _page = context.new_page()
        except Exception as e:
            return None, f"Failed to launch browser: {e}. Try: playwright install chromium"
    
    return _page, None

def browser_navigate(url: str) -> dict:
    """Navigate to URL - free"""
    page, err = _ensure_browser()
    if err:
        return {"success": False, "error": err}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        return {"success": True, "url": url, "title": page.title(), "content_length": len(page.content())}
    except Exception as e:
        return {"success": False, "error": str(e)}

def browser_click(selector: str) -> dict:
    """Click element by selector - free"""
    page, err = _ensure_browser()
    if err:
        return {"success": False, "error": err}
    try:
        page.click(selector, timeout=10000)
        return {"success": True, "selector": selector}
    except Exception as e:
        return {"success": False, "error": str(e)}

def browser_type(selector: str, text: str) -> dict:
    """Type text into element - free"""
    page, err = _ensure_browser()
    if err:
        return {"success": False, "error": err}
    try:
        page.fill(selector, text, timeout=10000)
        return {"success": True, "selector": selector, "text": text[:100]}
    except Exception as e:
        return {"success": False, "error": str(e)}

def browser_screenshot(path: str = "screenshot.png", full_page: bool = False) -> dict:
    """Screenshot - free"""
    page, err = _ensure_browser()
    if err:
        return {"success": False, "error": err}
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(p), full_page=full_page)
        return {"success": True, "path": str(p), "full_page": full_page}
    except Exception as e:
        return {"success": False, "error": str(e)}

def browser_extract(selector: str = "body") -> dict:
    """Extract text/HTML from selector - free"""
    page, err = _ensure_browser()
    if err:
        return {"success": False, "error": err}
    try:
        # Try text
        try:
            text = page.inner_text(selector, timeout=5000)
        except Exception:
            text = page.text_content(selector, timeout=5000) or ""
        html = ""
        try:
            html = page.inner_html(selector, timeout=5000)[:10000]
        except Exception:
            pass
        return {"success": True, "selector": selector, "text": text[:10000], "html": html[:10000]}
    except Exception as e:
        return {"success": False, "error": str(e)}

def browser_close() -> dict:
    """Close browser - free"""
    global _browser, _playwright, _page
    try:
        if _browser:
            _browser.close()
        if _playwright:
            _playwright.stop()
        _browser = None
        _playwright = None
        _page = None
        return {"success": True, "message": "Browser closed"}
    except Exception as e:
        return {"success": False, "error": str(e)}

# Tool definitions for free LLM
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "Navigate browser to URL - free Playwright, no API key, for web automation, page extraction",
            "parameters": {"type": "object", "properties": {"url": {"type": "string", "description": "URL to navigate"}}, "required": ["url"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click element by CSS selector in browser - free",
            "parameters": {"type": "object", "properties": {"selector": {"type": "string", "description": "CSS selector"}}, "required": ["selector"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into element - free browser automation",
            "parameters": {"type": "object", "properties": {"selector": {"type": "string"}, "text": {"type": "string"}}, "required": ["selector", "text"]}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_screenshot",
            "description": "Take screenshot of current page - free, saves to path",
            "parameters": {"type": "object", "properties": {"path": {"type": "string", "default": "screenshot.png"}, "full_page": {"type": "boolean", "default": False}}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_extract",
            "description": "Extract text and HTML from selector - free for page extraction, scraping",
            "parameters": {"type": "object", "properties": {"selector": {"type": "string", "default": "body"}}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_close",
            "description": "Close browser - free",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
]

# For easier import
TOOL_MAP = {
    "browser_navigate": browser_navigate,
    "browser_click": browser_click,
    "browser_type": browser_type,
    "browser_screenshot": browser_screenshot,
    "browser_extract": browser_extract,
    "browser_close": browser_close,
}
