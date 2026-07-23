# Checking the mine-py API contract

**Do not fetch `/openapi.json` from the deployed API.** It returns 404 there;
the schema route is disabled in that deployment. Parsing that 404 body yields
an empty document whose `paths` and `components` are missing, which silently
looks like "the field is not there" instead of an error.

## Generate the schema offline (no server needed)

```
cd <mine-py checkout> && uv run mine-export-openapi
```

Writes `data/mine_py/openapi/openapi.json`. This is the reliable way to read
exact field names, nullability, and `maxLength` when writing issuekit code
against a mine-py contract. Note `data/` is gitignored in that checkout, so a
regenerated schema will not show up as a diff.

## Confirm what the deployed server actually accepts

The generated schema describes the local source, not the deployment. To check
the running target, two read-only probes:

Response shape - fetch any issue and look at its keys:

```python
from pathlib import Path
from issuekit.config.settings import load_config
from issuekit.proposals.api import api_client
with api_client(load_config(Path("."))) as client:
    print(sorted(client.get_issue(<id>).keys()))
```

Request acceptance - send the candidate field with a deliberately invalid body
to a non-existent issue number. Validation runs before the endpoint, so nothing
mutates:

```python
r = client._send("POST", "/api/issues/<project>/issues/999999/submit",
                 json={"agent_model": "probe"},
                 headers={"Accept": "application/json",
                          "Authorization": f"Bearer {client.login()}"})
print(r.status_code, r.text)
```

Read the 422 detail. Only a `missing` entry for the required field means the
probed field was accepted. An `extra_forbidden` entry naming the probed field
means the deployment predates it. Every mine-py transition request schema sets
`extra="forbid"`, so an unknown field fails the whole request and the
transition does not happen.

`client._request` swallows the detail and reports only "Unprocessable Entity";
use `_send` as above when the detail is what you need.
