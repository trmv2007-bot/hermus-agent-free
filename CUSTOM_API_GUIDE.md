# Custom API Feature - Free - Add Any API as Tool

This is the free custom API feature for Hermus Agent Free - Add any REST API as a tool for your agent, no paywall, 100% free.

## Why?

Original Hermes has some integrations paywalled or via OpenRouter. This free version lets you add **any API** you want as a tool in 1 command.

Examples:
- Weather API (OpenWeatherMap free tier)
- Your own backend API
- Notion, GitHub, Slack, Discord webhooks
- Any public REST API
- Internal company API

## Quick Start

### Add a custom API via CLI

```bash
# Example 1: Free JSONPlaceholder for testing (free, no key)
python hermus.py api add \
  --name jsonplaceholder_post \
  --description "Get a fake post by ID from jsonplaceholder free API for testing" \
  --url "https://jsonplaceholder.typicode.com/posts/{id}" \
  --method GET \
  --param "id:Post ID 1-100"

# Example 2: Weather API (free tier OpenWeatherMap)
python hermus.py api add \
  --name weather_api \
  --description "Get current weather for a city" \
  --url "https://api.openweathermap.org/data/2.5/weather" \
  --method GET \
  --param "q:City name" \
  --param "appid:OpenWeatherMap API key" \
  --param "units:Units metric or imperial"

# Example 3: With Bearer Auth (your own API with token)
python hermus.py api add \
  --name my_backend_api \
  --description "Call my backend to get user data" \
  --url "https://myapi.example.com/users/{user_id}" \
  --method GET \
  --param "user_id:User ID" \
  --auth-type bearer \
  --auth-token "YOUR_TOKEN_HERE"

# Example 4: POST with API Key header
python hermus.py api add \
  --name notion_create_page \
  --description "Create a Notion page" \
  --url "https://api.notion.com/v1/pages" \
  --method POST \
  --header "Content-Type:application/json" \
  --header "Notion-Version:2022-06-28" \
  --auth-type bearer \
  --auth-token "secret_XXX" \
  --param "parent:Parent page ID" \
  --param "title:Page title"
```

### List custom APIs

```bash
python hermus.py api list
# Output:
# Custom APIs (2):
#  - jsonplaceholder_post: Get a fake post... | GET https://.../posts/{id} | enabled=True
#    Params: ['id']
#  - weather_api: Get current weather... | GET https://...
```

### Test custom API

```bash
python hermus.py api test jsonplaceholder_post --args '{"id": "1"}'
# Returns JSON from API
```

### Remove custom API

```bash
python hermus.py api remove jsonplaceholder_post
```

## How Agent Uses Custom APIs

Once added, custom APIs appear as tools for the free LLM, just like `web_search`, `file_read`, etc.

Example conversation:

```
You> Use weather_api to get weather for London with my API key XXX

Hermus> [Tool] weather_api({"q": "London", "appid": "XXX", "units": "metric"})
         Tool weather_api returned: {"main": {"temp": 15.5}, "weather": [{"description": "cloudy"}]}

         The weather in London is 15.5°C, cloudy.
```

The agent automatically:
1. Sees custom API tool definition (name, description, parameters)
2. Extracts parameters from your natural language
3. Calls API via `core/custom_api.py` execute_api()
4. Handles URL templating {city} -> replaced with actual value
5. Handles auth: Bearer, API Key header, Basic auth
6. Returns JSON or text truncated to 5000 chars

## URL Templating

Use `{param}` in URL for path parameters:

- URL: `https://api.example.com/users/{user_id}/posts/{post_id}`
- Params: `user_id`, `post_id`
- When you call with `{"user_id": "123", "post_id": "456"}`, URL becomes `.../users/123/posts/456`

Remaining params go as query string for GET or JSON body for POST.

## Auth Types (Free)

- `none` - No auth
- `bearer` - `Authorization: Bearer <token>` - `--auth-type bearer --auth-token YOUR_TOKEN`
- `apikey` - Custom header like `X-API-Key: value` - `--auth-type apikey --auth-key X-API-Key --auth-token VALUE`
- `basic` - Basic auth username/password - `--auth-type basic --auth-key username --auth-password pass`

## Storage

Custom APIs stored in `data/custom_apis.json` - simple JSON file, free, no DB cost:

```json
[
  {
    "name": "weather_api",
    "description": "Get weather",
    "url": "https://api.openweathermap.org/data/2.5/weather",
    "method": "GET",
    "headers": {},
    "parameters": {
      "q": {"type": "string", "description": "City name"},
      "appid": {"type": "string", "description": "API key"}
    },
    "auth": {"type": "none"},
    "id": "custom_weather_api_20260804_...",
    "created": "2026-08-04T...",
    "enabled": true
  }
]
```

## Free Examples You Can Add Now (No Paywall)

### 1. GitHub API (free, no key for public repos)
```bash
python hermus.py api add \
  --name github_user \
  --description "Get GitHub user info" \
  --url "https://api.github.com/users/{username}" \
  --param "username:GitHub username"
```

### 2. Open-Meteo Weather (free, no key needed at all!)
```bash
python hermus.py api add \
  --name open_meteo_weather \
  --description "Get weather forecast for lat/lon, free no key" \
  --url "https://api.open-meteo.com/v1/forecast" \
  --param "latitude:Latitude" \
  --param "longitude:Longitude" \
  --param "current_weather:Current weather bool"
```

### 3. Your Own Local API
```bash
python hermus.py api add \
  --name local_api \
  --description "My local dev server API" \
  --url "http://localhost:3000/api/{endpoint}" \
  --param "endpoint:Endpoint name"
```

## Gateway Integration

Custom APIs work in gateway too - Telegram/Discord bot can call your custom APIs:

```
User on Telegram: "Get weather for Tokyo"
Bot (Hermus Free): Uses weather_api custom tool + replies with weather
```

Cross-platform continuity: Add API once via CLI, it works in Telegram, Discord, CLI same memory.

## Security - Free

- Custom APIs run via `requests` with timeout 30s
- No shell execution
- Auth tokens stored in `data/custom_apis.json` - keep that file private (it's in .gitignore? No, we include it but you should not commit tokens - use env vars in future)
- For production, use env var substitution (future feature)

## Future Free Improvements

- [ ] Env var substitution in auth token: `{{GROQ_API_KEY}}`
- [ ] More methods: PATCH
- [ ] Webhook custom APIs (incoming)
- [ ] OpenAPI spec import - paste OpenAPI JSON and auto-create many APIs

## Test

```bash
python tests/test_custom_api.py
# Uses jsonplaceholder free API - no key needed
```
