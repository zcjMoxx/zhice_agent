# Travel integration and real smoke

`test_fake_e2e.py` starts only the deterministic local Fake MCP and exercises the real Web API entry, AgentLoop, Tool discovery, official SkillExecutor, `finalize_travel_plan`, RuntimeEvent and actor-scoped Store.

`test_external_smoke.py` never runs by default. Enable each real source separately with `ZHICE_TRAVEL_SMOKE_<SOURCE>=1` and provide the required runtime-only credentials. The complete manual browser smoke additionally requires:

- build the frontend with `VITE_AMAP_JS_API_KEY` and `VITE_AMAP_JS_SECURITY_CODE`;
- open `/travel` through a real Gateway login;
- generate a plan containing coordinates and verify markers/polyline load;
- temporarily block the JS API or use an invalid key and verify the textual route remains readable;
- for Xiaohongshu, run once with a valid isolated Cookie, then with an expired Cookie and `ZHICE_TRAVEL_SMOKE_XHS_EXPECT_AUTH_REQUIRED=1`.

The catalog tests are only the first external check. Before server release, also execute real business calls:

- AMap `maps_text_search` and one route tool such as `maps_direction_transit_integrated`;
- Tavily `tavily_search`, then `tavily_extract` using one returned URL;
- 12306 one real query-only ticket lookup;
- Xiaohongshu login status, search, detail and expired-Cookie degradation;
- browser-loaded AMap JS SDK with the separate Web JS key and security code.

Runtime `.env` is the durable local credential source. Do not rely on values set only in one PowerShell process. The AMap Web Service key (`AMAP_MAPS_API_KEY`) is not the same credential as the browser Web JS key (`VITE_AMAP_JS_API_KEY`) and security code (`VITE_AMAP_JS_SECURITY_CODE`).

No real API key, Cookie, OAuth token, Header or service URL is stored in this repository.
