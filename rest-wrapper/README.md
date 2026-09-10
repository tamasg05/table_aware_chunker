# REST wrapper

This directory wraps the two primary library operations in a stateless FastAPI
service:

- `POST /v1/extract` accepts PDF uploads or, when explicitly enabled, HTML URLs;
- `POST /v1/chunks` accepts the returned `blocks` and creates chunks; and
- `GET /status` reports service availability and build-time Git information;
- `GET /health` supports container and Kubernetes health probes.

No Python runtime is required in a consuming application pod. Only the REST
service image contains Python and the extraction dependencies.

## Run locally

From the repository root, install the wrapper requirements and the local
library, then start Uvicorn:

```powershell
python -m pip install -r .\rest-wrapper\requirements.txt
python -m pip install --no-deps -e .
uvicorn --app-dir .\rest-wrapper app:app --host 0.0.0.0 --port 8000
```

Interactive OpenAPI documentation is then available at
`http://localhost:8000/docs`.

Upload one or more PDFs by repeating the `files` form field:

```bash
curl -X POST http://localhost:8000/v1/extract \
  -F "files=@tests/artifacts/Q8.pdf" \
  -o extraction.json
```

The response contains `blocks`, `corpus_text`, `sources`, and stable corpus
identifiers. Send its `blocks` to the second endpoint:

```json
{
  "blocks": [
    {
      "type": "paragraph",
      "text": "Example text",
      "source_name": "example.pdf",
      "source_url": "",
      "page": 1,
      "heading_path": []
    }
  ],
  "strategy": "words",
  "chunk_size": 450,
  "chunk_overlap": 60,
  "min_text_chunk_size": 100,
  "max_text_page_span": 2
}
```

`min_text_chunk_size` is a best-effort word target for consolidating compatible
short text. If a page-local text group is shorter than this target, it may be
joined to text on the next page when both groups belong to the same source and
section. `max_text_page_span` limits how many consecutive pages one such group
may cover. Tables always end the text group, and setting
`min_text_chunk_size` to `0` disables cross-page text merging.

```bash
curl -X POST http://localhost:8000/v1/chunks \
  -H "Content-Type: application/json" \
  --data-binary @chunk-request.json
```

## Run as a container

Build from the repository root because the image installs the local library:

```bash
docker build -f rest-wrapper/Dockerfile -t table-aware-chunker-rest:0.1.0 .
docker run --rm -p 8000:8000 table-aware-chunker-rest:0.1.0
```

The simple build remains useful for local testing. Because no Git information
is supplied, its `/status` response contains `"unknown"` for the commit fields.
For a traceable image, pass the checked-out commit and its timestamp from Git
as build arguments:

```bash
docker build \
  --build-arg GIT_COMMIT="$(git rev-parse HEAD)" \
  --build-arg GIT_COMMIT_TIME="$(git show -s --format=%cI HEAD)" \
  -f rest-wrapper/Dockerfile \
  -t table-aware-chunker-rest:0.1.0 .
```

The image converts the commit ID to seven lowercase characters, normalizes the
commit time to UTC, and stores both values in `/app/rest-wrapper/build-info.json`.
It also records the full commit ID in the standard
`org.opencontainers.image.revision` image label. The `.git` directory is not
copied into the image and Git is not needed when the container runs.

After starting the container, inspect its version with:

```bash
curl http://localhost:8000/status
```

The response has the same operational shape as the other services:

```json
{
  "git": {
    "commit": {
      "id": {
        "abbrev": "50a44a3"
      },
      "time": "2026-09-04T10:46:37Z"
    }
  },
  "status": "UP"
}
```

`/status` returns `UP` only when the running application can answer the request.
The Git values describe the immutable image build. Production images should be
built by CI from a clean checkout; otherwise the reported commit does not
identify uncommitted working-tree changes included in the image.

The Dockerfile explicitly installs `rest-wrapper/requirements.txt` and then
installs the local `table-aware-chunker` project without resolving the same
dependencies a second time.

### Docker or Kubernetes?

Docker alone is sufficient for a simple environment. Running the command above
starts one container in which `app.py` is the REST application. Programs that
can reach the Docker host call `http://HOST:8000`.

Kubernetes is optional. Use it when the wrapper should run inside a cluster and
benefit from service discovery, health probes, resource limits, replica
management, and rolling updates. `kubernetes.yaml` contains an example
Deployment and ClusterIP Service. Replace its image value with the address of
the image in your registry, then apply it:

```bash
kubectl apply -f rest-wrapper/kubernetes.yaml
```

Other pods in the same namespace can call
`http://table-aware-chunker:8000/v1/extract` and
`http://table-aware-chunker:8000/v1/chunks`. Build information is available at
`http://table-aware-chunker:8000/status`.

## Configuration and security

| Environment variable | Default | Purpose |
| --- | ---: | --- |
| `TAC_MAX_PDF_FILES` | `10` | Maximum PDFs in one extraction request |
| `TAC_MAX_PDF_FILE_BYTES` | `50000000` | Maximum bytes in each uploaded PDF |
| `TAC_MAX_URL_PAGES` | `20` | Maximum URLs in one request |
| `TAC_MAX_URL_PAGE_BYTES` | `5000000` | Maximum downloaded bytes per URL |
| `TAC_URL_TIMEOUT_SECONDS` | `20` | Per-URL download timeout |
| `TAC_ALLOW_URL_SOURCES` | `false` | Enables extraction from supplied URLs |

URL extraction is disabled by default because accepting arbitrary URLs can
expose services reachable from the REST pod. Enable it only with suitable
egress restrictions and URL allow-listing outside this application. The sample
service does not provide authentication or TLS; place it behind your existing
cluster authorization, network policy, ingress, or API gateway where required.

Extraction uses temporary local files and returns the artifacts directly. The
service therefore requires no persistent volume, database, or shared filesystem.
