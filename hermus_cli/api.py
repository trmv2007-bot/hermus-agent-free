"""api — Custom API - add any API as tool (free)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._common import CLIContext


def register(subparsers) -> None:
    api_parser = subparsers.add_parser("api", help="Custom API - add any API as tool (free)")
    api_sub = api_parser.add_subparsers(dest="api_action")
    api_add = api_sub.add_parser("add", help="Add custom API")
    api_add.add_argument("--name", required=True, help="API tool name, e.g., weather_api")
    api_add.add_argument("--description", required=True, help="Description for LLM")
    api_add.add_argument(
        "--url", required=True, help="URL with optional {param} placeholders, e.g., https://api.example.com/weather/{city}"
    )
    api_add.add_argument("--method", default="GET", choices=["GET", "POST", "PUT", "DELETE"], help="HTTP method")
    api_add.add_argument("--header", action="append", help="Headers as Key:Value, can repeat")
    api_add.add_argument("--param", action="append", help="Parameters as name:description, e.g., city:City name, can repeat")
    api_add.add_argument("--auth-type", choices=["none", "bearer", "apikey", "basic"], default="none")
    api_add.add_argument("--auth-token", help="Bearer token or API key value")
    api_add.add_argument("--auth-key", help="API key header name (for apikey type) or username (for basic)")
    api_add.add_argument("--auth-password", help="Password for basic auth")

    api_sub.add_parser("list", help="List custom APIs")
    api_remove = api_sub.add_parser("remove", help="Remove custom API")
    api_remove.add_argument("name", help="API name or id")
    api_test = api_sub.add_parser("test", help="Test custom API")
    api_test.add_argument("name", help="API name")
    api_test.add_argument("--args", help='JSON args for test, e.g., \'{"city": "London"}\'')

    api_discover = api_sub.add_parser(
        "discover",
        help="Find useful APIs in the public-apis catalog (offline snapshot)",
    )
    api_discover.add_argument("query", nargs="?", default="", help="Task/API keywords, e.g. weather or threat intelligence")
    api_discover.add_argument("--category", default="", help="Category filter, e.g. Security")
    api_discover.add_argument("--auth", default="any", help="any, No, apiKey, OAuth, ...")
    api_discover.add_argument("--allow-http", action="store_true", help="Include APIs without confirmed HTTPS")
    api_discover.add_argument("--cors", default="any", choices=["any", "Yes", "No", "Unknown"])
    api_discover.add_argument("--limit", type=int, default=10)
    api_discover.add_argument("--refresh", action="store_true", help="Refresh the runtime catalog from GitHub first")
    api_sub.add_parser("categories", help="List public API categories and free/HTTPS counts")
    api_sub.add_parser("refresh-catalog", help="Refresh public API catalog from GitHub")


def run(args, ctx: CLIContext) -> None:

    from core.custom_api import custom_api_manager

    if args.api_action == "add":
        # Parse headers
        headers = {}
        if args.header:
            for h in args.header:
                if ":" in h:
                    k, v = h.split(":", 1)
                    headers[k.strip()] = v.strip()
        # Parse params
        params = {}
        if args.param:
            for p in args.param:
                if ":" in p:
                    k, v = p.split(":", 1)
                    params[k.strip()] = {"type": "string", "description": v.strip()}
                else:
                    params[p.strip()] = {"type": "string", "description": f"Parameter {p}"}
        # Auth
        auth = {"type": args.auth_type}
        if args.auth_type == "bearer":
            auth["token"] = args.auth_token
        elif args.auth_type == "apikey":
            auth["key"] = args.auth_key or "X-API-Key"
            auth["value"] = args.auth_token
        elif args.auth_type == "basic":
            auth["username"] = args.auth_key
            auth["password"] = args.auth_password

        api_def = {
            "name": args.name,
            "description": args.description,
            "url": args.url,
            "method": args.method,
            "headers": headers,
            "parameters": params,
            "auth": auth,
        }
        result = custom_api_manager.add_api(api_def)
        print(f"Add custom API result: {result}")
        if result.get("success"):
            print(f"✅ Custom API '{args.name}' added! Agent can now use it as tool.")
            print(f"   Try: hermus --model mock/mock and say 'Use {args.name} with ...'")

    elif args.api_action == "list":
        apis = custom_api_manager.list_apis()
        print(f"Custom APIs ({len(apis)}):")
        for api in apis:
            print(f" - {api['name']}: {api['description']} | {api['method']} {api['url']} | enabled={api.get('enabled', True)}")
            if api.get("parameters"):
                print(f"    Params: {list(api['parameters'].keys())}")

    elif args.api_action == "remove":
        result = custom_api_manager.remove_api(args.name)
        print(f"Remove result: {result}")

    elif args.api_action == "test":
        import json as json_lib

        test_args = {}
        if args.args:
            try:
                test_args = json_lib.loads(args.args)
            except Exception:
                print(f"Failed to parse --args JSON: {args.args}, using empty")
        result = custom_api_manager.execute_api(args.name, test_args)
        print(f"Test {args.name} with {test_args}:")
        if not result.get("success"):
            print(f"❌ error: {result.get('error')}")
            print(f"   url: {result.get('url')}")
        else:
            print(f"✅ HTTP {result.get('status_code')}")
            print(f"   url: {result.get('url')}")
            data = result.get("data_str") or result.get("data")
            if data:
                print(f"   data: {str(data)[:1000]}")
            if result.get("used_key"):
                print(f"   key used: {result['used_key']}")
                print(f"   keys for this API: {result.get('total_keys_for_this_api')}")

    elif args.api_action == "discover":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.search(
            query=args.query,
            category=args.category,
            auth=args.auth,
            https_only=not args.allow_http,
            cors=args.cors,
            limit=args.limit,
            refresh=args.refresh,
        )
        refresh = result.get("refresh")
        if refresh and not refresh.get("success"):
            print(f"⚠️ Refresh failed; using {refresh.get('using_fallback')}: {refresh.get('error')}")
        print(
            f"Public APIs matching '{args.query or '*'}': "
            f"{result.get('total_matched', 0)} found, showing {result.get('count', 0)}"
        )
        for item in result.get("results", []):
            print(f"\n - {item['name']} [{item['category']}]")
            print(f"   {item['description']}")
            print(f"   auth={item['auth']} https={item['https']} cors={item['cors']} | {item['documentation_url']}")
        catalog = result.get("catalog", {})
        print(f"\nSource: {catalog.get('source')} ({catalog.get('loaded_from')}, {catalog.get('total_apis')} APIs)")
        print("Note: links are documentation, not trusted endpoints. Review the docs, then use `hermus api add`.")

    elif args.api_action == "categories":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.categories()
        print(f"Public API categories ({result.get('count', 0)}):")
        for item in result.get("categories", []):
            print(
                f" - {item['category']}: total={item['total']} "
                f"no-auth={item['no_auth']} https={item['https']} cors={item['cors_yes']}"
            )

    elif args.api_action == "refresh-catalog":
        from tools.public_apis import public_api_catalog

        result = public_api_catalog.refresh()
        if result.get("success"):
            print(
                f"✅ Refreshed {result.get('count')} APIs across "
                f"{result.get('categories')} categories from {result.get('source')}"
            )
            print(f"   Runtime cache: {result.get('cache_path')}")
        else:
            print(f"❌ Refresh failed: {result.get('error')}")
            print(f"   Continuing with {result.get('using_fallback')} ({result.get('fallback_count')} APIs)")

    else:
        ctx.parser.parse_args(["api", "--help"])
