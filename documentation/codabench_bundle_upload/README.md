# Codabench Competition Bundle Upload Script

This script uploads a Codabench competition bundle ZIP file through the Codabench REST API.

It performs the following steps automatically:

1. Authenticate with Codabench
2. Create a competition bundle dataset
3. Upload the ZIP file
4. Finalize the upload
5. Wait for the competition to be created

## Requirements

Install dependencies:

```bash
pip install python-dotenv
```

Create a `.env`

```env
CODABENCH_BASE_URL=https://www.codabench.org
CODABENCH_TOKEN=your_token_here  # optional is you want to use your username and password to get token
```

## Usage

Using an API token from `.env`:

```bash
python codabench/upload_bundle.py competition_bundle.zip
```

Using username and password:

```bash
python codabench/upload_bundle.py \
  --username USERNAME \
  --password PASSWORD \
  competition_bundle.zip
```

Using a custom Codabench instance:

```bash
python codabench/upload_bundle.py \
  --base-url https://your-codabench-instance.org \
  competition_bundle.zip
```
