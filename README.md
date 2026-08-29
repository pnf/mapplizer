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
field 2 (repeated message)  one per entry, in one of two shapes

  a point of interest:
  |- field 1 (varint)       resultProviderId
  +- field 2 (varint)       muid

  a dropped pin:
  |- field 3 (string)       reverse-geocoded address
  +- field 4 (message)      |- field 1 (double) latitude
                            +- field 2 (double) longitude
```

So membership costs one redirect — no HTML parsing, no headless browser, no
JavaScript. Points of interest are then hydrated through `POST /data/place`,
the same private endpoint maps.apple.com's own bundle calls, which returns
names, coordinates, addresses, categories, phone numbers, ratings, hours and
photos. Dropped pins need no lookup at all — the URL already has everything.

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

## Two kinds of entry

A guide can contain **points of interest** (a business, with a muid) and
**dropped pins** (a user-placed marker at a coordinate, with no business behind
it). They are encoded differently and only the first kind needs a lookup.
`GuideRef.entries` keeps them in guide order; `.places` and `.pins` select
each kind.

## Why the completeness check matters

The guide page server-renders only the **first 20** places and lazy-loads the
rest. Anything that scrapes just the HTML produces a partial export that looks
complete. `mapplizer` compares the resolved count against the count declared in
the URL and **fails loudly** on a mismatch:

```console
$ mapplizer https://maps.apple/ug/...
error: exported 54 of 57 entries; 3 missing -- 3 failed to resolve (...).
Re-run with --allow-partial to export anyway.
```

**Dead references** are reported separately from failures. A guide can outlive
the places in it: if Apple returns HTTP 200 but simply omits a muid, that place
no longer exists in Apple's data and no retry will bring it back.
maps.apple.com silently drops these from its own rendering; `mapplizer` names
them instead, and `--allow-partial` exports the rest.

```console
error: exported 11 of 12 entries; 1 missing -- 1 no longer listed by Apple (13567334765449204772).
```

## Usage

```
mapplizer URL [-o OUTPUT] [-f {kml,kmz,json}] [--photos] [--lang LANG]
              [--cache DIR] [--chunk-size N] [--allow-partial] [-q] [-v]
```

- `--profile rego` (default) or `--profile earth` — see Output below.
- `-f kmz --photos` embeds photos in the archive. Required for photos in Rego.
- `--probe PATH` writes a diagnostic KMZ instead of exporting — see below.
- `-f json` dumps the normalized model — useful for debugging a bad export.
- `--cache DIR` stores raw place records by muid, so re-runs cost no network.
- `--lang` sets `Accept-Language`, which controls the language of the returned
  names, categories and addresses.

## Output

Two audiences want incompatible things from a placemark, so there are two
profiles, selected with `--profile`.

### `rego` (default)

What the [Rego](https://regoapp.com/) iOS app actually reads. This was
established by importing a probe file, not from documentation — Rego publishes
no KML mapping, and most of the obvious guesses turn out to be wrong:

| Convention | Result |
| --- | --- |
| `<address>` element | **ignored** |
| `<Data name="address">` | **ignored** |
| `<phoneNumber>` element | **ignored** |
| `gx_media_links` (Google My Maps) | **ignored** |
| `<img>` with a remote URL | **ignored** — remote images are never fetched |
| `<img>` with a KMZ-relative path | **the only photo channel** |
| plain-text `<description>` | shown as notes |
| HTML `<description>` | shown **raw**, tags and all |

Rego keeps only coordinates and derives its own address, so everything a reader
should see has to go in `<description>` as plain text, with photos as `<img>`
tags pointing inside the archive. Photos therefore require `-f kmz --photos`;
any other combination imports with no pictures, and the CLI says so.

A photo that fails to download is dropped from the description rather than
falling back to its URL, since a remote `<img>` would render as a stray tag.

### `earth`

The standards-shaped output: an HTML card in `<description>`, for Google Earth
and GIS tools.

### Both profiles

Both still emit the native `<address>` and `<phoneNumber>` elements and the
full typed `<ExtendedData>`. Rego discards them, but they cost nothing, they
are correct KML, and other tools do read them.

KML 2.2 makes `AbstractFeatureType` a **sequence**, so element order is fixed:
name, address, phoneNumber, Snippet, description, styleUrl, Region,
ExtendedData, then geometry. Out of order, the file still parses but fails
schema validation. Tests validate output against the official `ogckml22.xsd`.

### Finding out what an importer reads

Most apps do not document their KML mapping. `--probe` writes a diagnostic KMZ
whose pins each exercise one convention in isolation, with the answer in the
pin's own name — import it and the surviving pins name the supported
conventions.

```console
$ mapplizer --probe probe.kmz                      # where metadata can live
$ mapplizer --probe probe.kmz --probe-set photos   # details of the <img> channel
```

## Caveats

- `/data/place` is a **private endpoint** with no stability guarantee. It is
  currently unauthenticated, though `shell.js` wraps it in a captcha gate that
  can presumably be armed. Use the cache, keep batches small, be polite.
- Only **user-created** guides (`maps.apple/ug/...`) are supported. Editorial
  and publisher guides are served from `/data/curated-collection` and would
  need a second resolver.
- muids are uint64 and exceed JavaScript's safe integer range. They are strings
  everywhere in this codebase; keep them that way.
- A guide's declared entry count is the only reliable total. The rendered page
  agrees with it only after lazy-loading finishes, and it silently omits dead
  references.

## Development

```console
$ pip install -e '.[dev]'
$ pytest
```

Tests run entirely offline against saved fixtures in `tests/fixtures/`.

Verified end to end against five real guides — 100 placemarks total, including
a dropped pin and a dead reference, with accented names, typographic
apostrophes and ampersands round-tripping intact.
