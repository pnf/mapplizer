# mapplizer

Export an Apple Maps guide share link to KML or KMZ.

```console
$ mapplizer https://maps.apple/ug/yDaPfLzD_27SbtQL_DSGUA
guide 'Resto' declares 57 places
fetched 57/57 places
Wrote 57 places to Resto.kml
```

## How it works

A share link like `https://maps.apple/ug/yDaPfLzD_27SbtQL_DSGUA` is a 301 to
`https://maps.apple.com/guides?user=<base64 protobuf>`. That parameter holds the
entire guide:

```
field 1 (string)            guide name
field 2 (repeated message)  one per place
  |- field 1 (varint)       resultProviderId
  +- field 2 (varint)       muid
```

So membership costs one redirect — no HTML parsing, no headless browser, no
JavaScript. Each place is then hydrated through `POST /data/place`, the same
private endpoint maps.apple.com's own bundle calls, which returns names,
coordinates, addresses, categories, phone numbers, ratings, hours and photos.

The pipeline is five stages, each independently testable:

| Stage | Module | Job |
| --- | --- | --- |
| Resolve | `resolve.py` | follow the redirect, find the `user` parameter |
| Decode | `protobuf.py` | minimal varint reader — no codegen dependency |
| Hydrate | `fetch.py` | batched `POST /data/place`, retries, disk cache |
| Normalize | `normalize.py` | flatten Apple's `component` array into `Place` |
| Emit | `kml.py` | KML / KMZ |

`normalize.py` is the only module that knows Apple's schema; everything
downstream speaks `mapplizer.model`.

## Why the completeness check matters

The guide page server-renders only the **first 20** places and lazy-loads the
rest. Anything that scrapes just the HTML produces a partial export that looks
complete. `mapplizer` compares the resolved count against the count declared in
the URL and **fails loudly** on a mismatch:

```console
$ mapplizer https://maps.apple/ug/...
error: resolved 54 of 57 places (3 missing). Re-run with --allow-partial to export anyway.
```

## Usage

```
mapplizer URL [-o OUTPUT] [-f {kml,kmz,json}] [--photos] [--lang LANG]
              [--cache DIR] [--chunk-size N] [--allow-partial] [-q] [-v]
```

- `-f kmz` zips the output; add `--photos` to download and embed place photos
  rather than linking them remotely.
- `-f json` dumps the normalized model — useful for debugging a bad export.
- `--cache DIR` stores raw place records by muid, so re-runs cost no network.
- `--lang` sets `Accept-Language`, which controls the language of the returned
  names, categories and addresses.

## Output

Each place becomes a `<Placemark>` with a `<Point>`. The readable card goes in
`<description>` as CDATA'd HTML; every structured field is repeated in
`<ExtendedData>` so nothing is lost round-tripping into other tools.

## Caveats

- `/data/place` is a **private endpoint** with no stability guarantee. It is
  currently unauthenticated, though `shell.js` wraps it in a captcha gate that
  can presumably be armed. Use the cache, keep batches small, be polite.
- Only **user-created** guides (`maps.apple/ug/...`) are supported. Editorial
  and publisher guides are served from `/data/curated-collection` and would
  need a second resolver.
- muids are uint64 and exceed JavaScript's safe integer range. They are strings
  everywhere in this codebase; keep them that way.

## Development

```console
$ pip install -e '.[dev]'
$ pytest
```

Tests run entirely offline against saved fixtures in `tests/fixtures/`.
