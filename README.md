# StoryHD — WhatsApp Status Video Optimizer

A deploy-ready Flask + FFmpeg application. It accepts MP4/MOV/M4V files, inspects the source, re-encodes to broadly compatible H.264/AAC MP4, preserves aspect ratio and source frame rate, provides a preview and a download, and removes temporary media after 30 minutes.

## Important quality note

This application optimizes the video **before** it is uploaded to WhatsApp. It cannot disable or bypass WhatsApp's own processing/compression after upload, so it cannot guarantee zero quality loss in WhatsApp Status. It avoids unnecessary upscaling, keeps the original orientation/aspect ratio, and uses H.264 CRF 18 with AAC audio for a high-quality compatible output.

## Run locally with Docker

```bash
docker build -t storyhd .
docker run --rm -p 10000:10000 -e PORT=10000 storyhd
```

Then open `http://localhost:10000`.

## Run locally with Python

Requirements: Python 3.12+, FFmpeg and ffprobe installed and available on PATH.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:5000`.

## Deploy to Render

1. Create a GitHub repository and upload the contents of this folder.
2. In Render, choose **New + → Blueprint** and connect that repository.
3. Render detects `render.yaml` and the Dockerfile. Review the service, then deploy.
4. After deployment, open the generated `https://<service-name>.onrender.com` address and test `/health`.

The included Render blueprint starts on the **Free** web-service plan to avoid selecting a paid plan without your approval. Render Free services can spin down when idle, can restart, have no persistent disk, and have limited compute (512 MB RAM and less than 1 CPU). This may make video processing slow or cause larger uploads/encodes to fail. Check Render's current limits and pricing before upgrading; paid compute incurs charges. For a real public service, monitor memory, CPU, processing time, and costs. Use a persistent job queue and object storage if scaling beyond a small personal project.

## Configuration

- `MAX_FILE_BYTES`: maximum upload size (default 300 MiB)
- `MAX_DURATION_SECONDS`: max video duration (default 180 seconds)
- `JOB_TTL_SECONDS`: how long outputs remain available (default 1800 seconds)
- `PROCESSING_WORKERS`: background FFmpeg jobs (default 2)
- `MAX_ACTIVE_JOBS`: maximum queued/processing jobs accepted at once (default 4)
- `STORYHD_DATA_DIR`: temporary storage root (default `/tmp/storyhd-data`)

## API

- `GET /health` — service and FFmpeg health
- `POST /api/optimize` — multipart form field `video`, returns a job ID
- `GET /api/jobs/<job_id>` — queued/processing/done/error status
- `GET /download/<job_id>` — download the MP4 after success

## Security and operational notes

- Filenames are replaced with random server-side IDs.
- The server checks the file with `ffprobe` before processing; file extensions alone are not trusted.
- Temporary originals are removed after processing; generated output and job metadata are cleaned after the configured TTL when subsequent requests arrive.
- This is a small starter deployment, not a hardened high-scale multi-tenant service. Before public launch, add rate limiting, abuse protection, per-IP quotas, structured monitoring, and a scheduled cleanup process. Do not expose it to untrusted traffic without those controls.
