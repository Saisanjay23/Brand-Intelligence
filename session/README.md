# session/

One file per platform, named after the platform id in `core/registry.py`:

```
session/facebook.json
```

**These are live credentials.** Anyone holding one of these files is logged in
as that account. They are gitignored — keep them that way, and use a dedicated
research account rather than a personal one.

## Exporting

Log into the platform in a browser, then export its cookies with a
Cookie-Editor style extension (Export → JSON) into the file above. For Facebook
the export must include `c_user` and `xs`; the scanner refuses to start without
them.

Accepted formats: a JSON array of cookie objects, a `storage_state` object with
a `cookies` key, Netscape `cookies.txt`, or a raw `Cookie:` header string.

## Rotation

Sessions expire and can be checkpointed. Symptoms: rows come back
`LOGIN_REQUIRED` or `CHECKPOINT`, or `GET /api/platforms` stops reporting
`ready`. Re-export the file; nothing else needs to change.

Check one without running a scan:

```bash
python cli.py --check-session
```
