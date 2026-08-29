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

- `-f kmz` zips the output; add `--photos` to download and embed place photos
  rather than linking them remotely, and `--photo-links remote` if the target
  app cannot resolve KMZ-relative paths.
- `--probe PATH` writes a diagnostic KMZ instead of exporting — see below.
- `-f json` dumps the normalized model — useful for debugging a bad export.
- `--cache DIR` stores raw place records by muid, so re-runs cost no network.
- `--lang` sets `Accept-Language`, which controls the language of the returned
  names, categories and addresses.

## Output

Importers such as [Rego](https://regoapp.com/) map a placemark onto their own
place model, so metadata has to land in the fields they look for rather than
being flattened into a block of description HTML:

| Data | KML |
| --- | --- |
| name | `<name>` |
| address | `<address>` — a native KML 2.2 feature element |
| phone | `<phoneNumber>` — likewise native |
| notes | `<description>`, as plain text |
| photos | `<Data name="gx_media_links">` — the Google My Maps convention: URLs (or KMZ-relative paths) separated by spaces |
| everything else | typed `<Data>` entries in `<ExtendedData>` |

KML 2.2 makes `AbstractFeatureType` a **sequence**, so these elements have a
required order: name, address, phoneNumber, Snippet, description, styleUrl,
Region, ExtendedData, then the geometry. Out of order, the file still parses
but fails schema validation and some importers reject it. Every export is
validated against the official `ogckml22.xsd` in CI-able tests.

### Photos

`-f kmz --photos` downloads the photos into the archive and points
`gx_media_links` at the archive-relative copies. An importer that ignores
KMZ-relative paths needs `--photo-links remote` instead: the photos are still
embedded, but the links keep pointing at the original URLs.

### Finding out what an importer actually reads

Most apps do not document their KML mapping. `--probe` writes a diagnostic KMZ
whose pins each exercise one convention in isolation, with the answer in the
pin's own name:

```console
$ mapplizer --probe probe.kmz
```

```
01 address element                 06 photo two gx_media_links
02 ExtendedData address            07 photo description img tag
03 phoneNumber element             08 photo description img embedded
04 photo gx_media_links remote     09 description plain text
05 photo gx_media_links embedded   10 description html
```

Import it, see which pins came through with an address, a phone number, a
photo or notes, and the surviving pin names name the conventions that app
supports.

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
