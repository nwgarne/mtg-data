# mtg-data: `data` branch

This branch is auto-updated daily by the [refresh workflow](https://github.com/nwgarne/mtg-data/actions/workflows/refresh.yml). It is **force-pushed with no history** and exists solely to provide stable `raw.githubusercontent.com` URLs for consumers that cannot follow GitHub Release download redirects (Release assets 302 to a separate host that some sandboxes block).

Stable URLs:

```
https://raw.githubusercontent.com/nwgarne/mtg-data/data/mtg.db.gz
https://raw.githubusercontent.com/nwgarne/mtg-data/data/manifest.json
```

These serve the identical artifact as the [`latest` Release](https://github.com/nwgarne/mtg-data/releases/tag/latest), on the same daily refresh schedule. See the [main branch README](https://github.com/nwgarne/mtg-data/blob/main/README.md) for schema and usage.
