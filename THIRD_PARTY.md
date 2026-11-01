# Third-party data

The integration ships data made from these sources. Nothing is downloaded at runtime: the files below are built
offline and kept in the repository.

## Airport time zones: `custom_components/boarding_pass_tracker/core/airport_zones.json`

The IANA time zone of each airport of `airports.json`, used to turn the departure and arrival times typed in
«Aggiungi volo» and «Modifica volo» into instants. An airport without a zone refuses typed times only.

It is built by `tools/build_airport_zones.py` from two pinned wheels from PyPI. The tool checks each wheel's
sha256 before reading it as a ZIP archive; it imports and runs nothing from them. The versions and digests are also
in the file's `_airportsdata` and `_tzdata` keys.

| Source | Version | sha256 of the wheel | What is used |
|---|---|---|---|
| [airportsdata](https://github.com/mborsetti/airportsdata) (MIT) | 20261030, `airportsdata-20261030-py3-none-any.whl` | `0b50327f8d026be56cc18e1b83df356d2550f26127e13518883dc0f62533eb87` | `airportsdata/airports.csv` (sha256 `d3f74097b44bd8d2a1ff895d5746e15bbed48c9e183250ca34c34cbfd3cf318a`): the `tz` of an IATA code, when that row is the same airport as ours |
| [tzdata](https://github.com/python/tzdata), the IANA time zone database | 2026.6 (IANA 2026f), `tzdata-2026.6-py2.py3-none-any.whl` | `0a10eec6e13ef16b627fabed9a5f2ba61a62b1c67f327305f4e7e412a70b4aec` | `zoneinfo/zone.tab` (public domain, sha256 `e2e8e31c4e10f44576c79345e76060591dbb3eef6ce14e006afc8a373caa9593`): the zones of each country. The zone files are read only while building, to compare UTC offsets. They are not shipped. The package's own code is Apache-2.0 and is not used |

To rebuild the file, or to check that it is reproducible:

```sh
python3 -m pip download --no-deps --only-binary=:all: airportsdata==20261030 tzdata==2026.6 -d <empty folder>
python3 -I -B tools/build_airport_zones.py \
    --airportsdata <folder>/airportsdata-20261030-py3-none-any.whl \
    --tzdata <folder>/tzdata-2026.6-py2.py3-none-any.whl --check --report
```

`--check` compares a fresh build with the committed file and changes nothing. Without it, the tool writes the
file. The rules are in the tool's docstring:
- airportsdata's zone, when its coordinates are within 300 km of ours and the zone agrees with the airport's
  country;
- else the country's only zone in `zone.tab`;
- else no zone.

The 20261030 build gives 5114 zones from airportsdata and 85 from `zone.tab`. 135 airports have no zone.

### airportsdata licence

```
The MIT License (MIT)

Copyright (c) 2020- Mike Borsetti <mike@borsetti.com>

This project includes data from https://github.com/mwgg/Airports Copyright
(c) 2014 mwgg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Airport coordinates: `custom_components/boarding_pass_tracker/core/airports.json`

The coordinates and countries come from [OurAirports](https://ourairports.com/data/) (public domain), as the
README says.
